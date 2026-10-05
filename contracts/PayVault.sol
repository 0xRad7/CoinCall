// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

/// @title PayVault — BOT Chain 上手工复刻 EIP-3009/x402 语义的结算合约（04_settlement §2）。
/// @notice 消费者离线签 EIP-712 `Authorization`，operator(keeper) 批量执行 transferFrom
///         把 USDT 从消费者钱包划入本合约并按 provider 记应收账本；Provider 随时提现。
///         资金永不过平台手（铁律 P7），提现路径永开（铁律 P8：无 pause / 无 owner 锁 / 无时间锁）。
/// @dev 安全不变量：
///      I1 operator 无任何把合约内 token 转给"非 credits 对应 provider"的路径（唯一转出=providerWithdraw）；
///      I2 同一 nonce 永不成功扣款两次；
///      I3 消费者资金主权在 USDT 层（approve/transfer），本合约无从干预；
///      I4 token.balanceOf(本合约) == totalCredits == Σ 未提现 credits。
contract PayVault {
    // ---- 常量与类型 ---------------------------------------------------------
    uint256 public constant MAX_BATCH = 50;

    /// @dev EIP-3009 TransferWithAuthorization 同构六元组；digest 字段序即声明序。
    struct Authorization {
        address from; // 消费者钱包（扣款人）
        address to; // 收款方，必须 == 本合约（资金落账与记账恒等的前提）
        uint256 value; // 金额（最小单位）
        uint256 validAfter; // 生效时间窗下界（含）
        uint256 validBefore; // 生效时间窗上界（含）
        uint256 nonce; // 一次性随机数，成功扣款后烧毁（I2）
    }

    struct ChargeCall {
        address provider; // 入账的 Provider 收款方
        Authorization auth;
        uint8 v;
        bytes32 r;
        bytes32 s;
    }

    // ---- 存储 ---------------------------------------------------------------
    address public operator; // keeper，唯一可调 chargeWithSigBatch
    address public immutable token;
    bytes32 public immutable DOMAIN_SEPARATOR;
    uint256 public totalCredits; // Σ 未提现 credits（I4 审计视图：链上即对账）
    mapping(address => uint256) public credits; // provider => 应收账款
    mapping(uint256 => bool) public usedNonces; // nonce => 已成功扣款（I2）

    // ---- 事件 ---------------------------------------------------------------
    event Charged(address indexed provider, address indexed from, uint256 value, uint256 indexed nonce);
    event ChargeFailed(address indexed provider, address indexed from, string reason);
    event Withdrawn(address indexed provider, address indexed to, uint256 amount);
    event OperatorUpdated(address indexed oldOperator, address indexed newOperator);

    // ---- 错误 ---------------------------------------------------------------
    error NotOperator();
    error BatchTooLarge(uint256 size);
    error InsufficientCredits(address provider, uint256 available, uint256 requested);
    error BadWithdrawTarget();
    error WithdrawTransferFailed();

    bytes32 private constant _DOMAIN_TYPE_HASH =
        keccak256("EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)");
    bytes32 private constant _AUTH_TYPE_HASH =
        keccak256("Authorization(address from,address to,uint256 value,uint256 validAfter,uint256 validBefore,uint256 nonce)");
    bytes4 private constant _TRANSFER_SELECTOR = 0xa9059cbb; // transfer(address,uint256)
    bytes4 private constant _TRANSFER_FROM_SELECTOR = 0x23b872dd; // transferFrom(address,address,uint256)

    constructor(address token_, address operator_) {
        token = token_;
        operator = operator_;
        DOMAIN_SEPARATOR = keccak256(
            abi.encode(
                _DOMAIN_TYPE_HASH, keccak256(bytes("PayVault")), keccak256(bytes("1")), block.chainid, address(this)
            )
        );
    }

    // ---- keeper 批量结算 ----------------------------------------------------
    /// @notice 逐笔：时间窗 → nonce 未用 → 验签 → transferFrom → 记账。
    ///         单笔失败只跳过并 emit ChargeFailed（reason 稳定短码），不回滚整批。
    /// @dev nonce 仅在扣款成功后烧毁：transfer_failed 笔可由 keeper 携原签名重试（04 §3）；
    ///      同批内重复 nonce 会被 ② 拦下，跨交易重放同理（I2）。
    function chargeWithSigBatch(ChargeCall[] calldata calls) external onlyOperator {
        uint256 n = calls.length;
        if (n > MAX_BATCH) revert BatchTooLarge(n);
        for (uint256 i = 0; i < n; ++i) {
            ChargeCall calldata c = calls[i];
            if (block.timestamp < c.auth.validAfter || block.timestamp > c.auth.validBefore) {
                emit ChargeFailed(c.provider, c.auth.from, "not_in_window"); // ① 时间窗
                continue;
            }
            if (usedNonces[c.auth.nonce]) {
                emit ChargeFailed(c.provider, c.auth.from, "nonce_used"); // ② 一次性 nonce
                continue;
            }
            if (c.auth.to != address(this)) {
                emit ChargeFailed(c.provider, c.auth.from, "auth_to_mismatch"); // ③ 收款方必须是本合约
                continue;
            }
            bytes32 digest = keccak256(abi.encodePacked("\x19\x01", DOMAIN_SEPARATOR, _hashAuthorization(c.auth)));
            address recovered = ecrecover(digest, c.v, c.r, c.s);
            if (recovered == address(0) || recovered != c.auth.from) {
                emit ChargeFailed(c.provider, c.auth.from, "bad_signature"); // ④ 验签
                continue;
            }
            if (!_transferIn(c.auth.from, c.auth.value)) {
                emit ChargeFailed(c.provider, c.auth.from, "transfer_failed"); // ⑤ 扣款
                continue;
            }
            usedNonces[c.auth.nonce] = true;
            credits[c.provider] += c.auth.value;
            totalCredits += c.auth.value;
            emit Charged(c.provider, c.auth.from, c.auth.value, c.auth.nonce);
        }
    }

    // ---- Provider 提现（铁律 P8：恒开） -------------------------------------
    /// @notice 只能提 msg.sender 自己的 credits；无 pause、无 owner 锁、无时间锁。
    /// @param to 收款地址（网关侧缺省建议 = provider 的 agentWallet）
    /// @param amount 提现额度，不得超过 credits[msg.sender]
    function providerWithdraw(address to, uint256 amount) external {
        if (to == address(0)) revert BadWithdrawTarget();
        uint256 available = credits[msg.sender];
        if (amount > available) revert InsufficientCredits(msg.sender, available, amount);
        credits[msg.sender] = available - amount;
        totalCredits -= amount;
        (bool ok, bytes memory ret) = token.call(abi.encodeWithSelector(_TRANSFER_SELECTOR, to, amount));
        if (!ok || (ret.length != 0 && !abi.decode(ret, (bool)))) revert WithdrawTransferFailed();
        emit Withdrawn(msg.sender, to, amount);
    }

    /// @notice operator 轮换（单步；04 §2 的 two-step 为可选项，未采用——见 CONSTRAINTS.md）。
    /// @dev operator 与资金安全无关（I1），最坏情况=扣款停摆，提现不受影响。
    function operatorUpdate(address newOperator) external onlyOperator {
        address old = operator;
        operator = newOperator;
        emit OperatorUpdated(old, newOperator);
    }

    // ---- 内部 ---------------------------------------------------------------
    function _hashAuthorization(Authorization calldata a) private pure returns (bytes32) {
        return keccak256(
            abi.encode(_AUTH_TYPE_HASH, a.from, a.to, a.value, a.validAfter, a.validBefore, a.nonce)
        );
    }

    /// @dev 低阶 call 兼容非标准 ERC-20（如无 bool 返回值的 USDT 变体）：无返回数据视为成功。
    function _transferIn(address from, uint256 value) private returns (bool) {
        (bool ok, bytes memory ret) = token.call(
            abi.encodeWithSelector(_TRANSFER_FROM_SELECTOR, from, address(this), value)
        );
        return ok && (ret.length == 0 || abi.decode(ret, (bool)));
    }

    modifier onlyOperator() {
        if (msg.sender != operator) revert NotOperator();
        _;
    }
}
