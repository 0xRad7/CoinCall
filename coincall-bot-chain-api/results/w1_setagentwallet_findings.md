# W1 定案：setAgentWallet 签名格式（源码实证）

> 依据：`results/d3_probes/src_IdentityRegistry_impl.json`（IdentityRegistryUpgradeable @ impl `0xdb74b629fe35cab514edc310dc9ad0ff68a1eb7c`，
> 经 EIP-1967 slot 从代理 `0xec8fFbC3c9A34AdDCbB3A14F91db2bd26A8b99c0` 直读取得，编译器 v0.8.24，OZ 5.4.0）。
> 规范预判（coincall-docs/05_botchain_api_ext.md §3）与本定案的出入见文末偏差表（→ CONSTRAINTS C-23）。

## 1. 函数完整签名

```solidity
function setAgentWallet(
    uint256 agentId,
    address newWallet,
    uint256 deadline,
    bytes calldata signature
) external   // nonpayable，无返回值
```

- 第三参数名是 **`deadline`**（探测 ABI 同名），不是 05 文档写的 `validAfter`。
- 合约常量：`MAX_DEADLINE_DELAY = 5 minutes`；`ERC1271_MAGICVALUE = 0x1626ba7e`。

## 2. 签名构造：EIP-712 typed data v4（不是 EIP-191）

```solidity
AGENT_WALLET_SET_TYPEHASH =
    keccak256("AgentWalletSet(uint256 agentId,address newWallet,address owner,uint256 deadline)");
//  = 0x678b53cd718d595370ab070ebf48edfdcd834beac116bf23e625fc7f4d5b7d32

structHash = keccak256(abi.encode(AGENT_WALLET_SET_TYPEHASH, agentId, newWallet, owner, deadline));
//                                   bytes32            uint256  address   address uint256
digest = _hashTypedDataV4(structHash);   // = keccak256("\x19\x01" ‖ domainSeparator ‖ structHash)
```

**消息哈希拼接字段**：`agentId`、`newWallet`、**`owner`（即 ownerOf(agentId)，调用方无法自选）**、`deadline`。
没有 salt、没有 nonce。

**EIP-712 domain**（`initialize()` → `__EIP712_init("ERC8004IdentityRegistry", "1")`；OZ 5.4 EIP712Upgradeable
每次调用重算、无缓存、无 salt；`address(this)` 在 delegatecall 下 = **代理地址**）：

| 字段 | 值 |
|---|---|
| name | `ERC8004IdentityRegistry` |
| version | `1` |
| chainId | `block.chainid`（测试网 968） |
| verifyingContract | `0xec8fFbC3c9A34AdDCbB3A14F91db2bd26A8b99c0`（代理） |
| salt | 无 |

domain typeHash 字符串：`EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)`。

测试网 968 的 domainSeparator（本仓库算法复算，与 eth_account `encode_typed_data` 逐字节一致）：

```
0x2afa5c5221b1fc50a3423f69446f07999dafe2a83724420ab5a34586a29362cf
```

黄金向量（agentId=137, newWallet=0x1111…1111, owner=0x2222…2222, deadline=1759500000）：

```
digest = 0xf147e6850760a6352660c6c50863e20aa6b56798db025d3a49c010fe96844474
```

合约还实现了 IERC5267 `eip712Domain()` 视图（返回 fields/name/version/chainId/verifyingContract/salt/extensions），
**可在线上做字段级对账**（needs_funds 用例已用）。

## 3. 谁有权签：**新钱包（newWallet）本人**，不是身份 owner

```solidity
(address recovered, err,) = ECDSA.tryRecover(digest, signature);
if (err != NoError || recovered != newWallet) {
    // ECDSA 失败 → ERC-1271（智能合约钱包）
    (ok, res) = newWallet.staticcall(abi.encodeCall(IERC1271.isValidSignature, (digest, signature)));
    require(ok && res.length >= 32 && abi.decode(res,(bytes4)) == 0x1626ba7e, "invalid wallet sig");
}
```

- 签名者必须能被恢复为 **newWallet 自己**（ECDSA EOA，或 newWallet 合约的 ERC-1271）——
  签名的语义是「新钱包同意被绑定为该 agent 的收款地址」（防抢绑：谁拥有新地址谁说了算）。
- 身份 owner **不参与签名**；owner（或 `isApprovedForAll`/`getApproved` 授权者）必须是**交易的 msg.sender**：

```solidity
require(msg.sender == ownerOf(agentId)
     || isApprovedForAll(owner, msg.sender)
     || msg.sender == getApproved(agentId), "Not authorized");
```

- 其余 require：`newWallet != address(0)`（"bad wallet"）。

**对本服务端点的含义**：`wallet_address` 是服务 keystore 代管账户（或出资账户）时，服务用该账户私钥
对 EIP-712 digest 签名（`core/tx.resolve_signer` 同一套解析）；否则调用方必须自带 `signature`
（EOA 的 65 字节 ECDSA 签名或合约钱包的 ERC-1271 签名）。交易发送者另需是 owner/被授权者。

## 4. 重放防护 / 过期检查

```solidity
require(block.timestamp <= deadline, "expired");
require(deadline <= block.timestamp + MAX_DEADLINE_DELAY, "deadline too far");   // ≤5 分钟
```

- **有过期检查，无 nonce**：deadline 必须落在 `[now, now+300s]` 窗口内（5 分钟上限）。
- 窗口内签名可被重放，但重放只能由 owner/被授权者发起且效果幂等（再次写入同一 wallet），风险受控；
  NFT 转移时 `_update` 会清空 agentWallet（防旧 owner 的钱包设置跟着身份走）。
- **空签名（`0x`）永远不可用**：tryRecover 对空 sig 返回错误 → 走 ERC-1271 staticcall 到 EOA 返回空 →
  revert "invalid wallet sig"。05 文档「signature=0x + validAfter=0 部分部署可用」的预判不成立（见 C-23）。

## 5. 与 05 文档预判的偏差（→ CONSTRAINTS.md §D C-23）

| 项 | 05_botchain_api_ext.md §3 预判 | 源码实证 |
|---|---|---|
| 签名格式 | EIP-191，owner 对 (agentId,newWallet,validAfter) 签 | **EIP-712 v4**，AgentWalletSet 四字段含 owner |
| 签名者 | 身份 owner | **newWallet 本人**（ECDSA 恢复==newWallet 或其 ERC-1271） |
| 第三参数 | validAfter（起点语义） | **deadline**（过期语义，窗口 ≤ now+300s） |
| 简化路径 | signature=0x + validAfter=0 可能可用 | **不可用**，空签名必 revert |
| msg.sender | — | owner / approvedForAll / approved 三者之一 |

附带源码事实（供端点 B/后续用）：
- `register()` 全部重载都会先把 `agentWallet` 初始化为 msg.sender，再 mint；
- `unsetAgentWallet(uint256)` 存在（同权限检查，无签名）；
- `setMetadata(uint256,string,bytes)` 禁止 key=="agentWallet"（保留键）；
- 元数据无键枚举接口（`mapping(string=>bytes)`），聚合视图只能按键取。

## 6. 落地位置

- 算法实现：`app/core/eip712.py`（domainSeparator/digest/sign，黄金向量单测锁定）；
- ABI 精录：`app/core/abis/erc8004.py`（setAgentWallet/unsetAgentWallet/setMetadata/eip712Domain）；
- 端点：`app/modules/erc8004.py`（POST /agent-identity/{token_id}/wallet）。
