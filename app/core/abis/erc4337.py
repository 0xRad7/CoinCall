"""ERC-4337：SimpleAccountFactory（已验证 ABI）+ SimpleAccount execute + EntryPoint v0.7 事件。"""

# 由 results/d3_probes 探测的已验证 ABI 精简生成（来源：scan.bohr.life getsourcecode，2026-10-01）

SIMPLE_ACCOUNT_FACTORY_ABI: list[dict] = [
    {
        "inputs": [],
        "name": "accountImplementation",
        "outputs": [{"internalType": "contract SimpleAccount", "name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [
            {"internalType": "address", "name": "owner", "type": "address"},
            {"internalType": "uint256", "name": "salt", "type": "uint256"},
        ],
        "name": "createAccount",
        "outputs": [{"internalType": "contract SimpleAccount", "name": "ret", "type": "address"}],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [
            {"internalType": "address", "name": "owner", "type": "address"},
            {"internalType": "uint256", "name": "salt", "type": "uint256"},
        ],
        "name": "getAddress",
        "outputs": [{"internalType": "address", "name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    },
]

# SimpleAccount（eth-infinitism 标准）execute 入口
SIMPLE_ACCOUNT_EXECUTE_ABI: list[dict] = [
    {
        "name": "execute",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "dest", "type": "address"},
            {"name": "value", "type": "uint256"},
            {"name": "func", "type": "bytes"},
        ],
        "outputs": [],
    }
]

# EntryPoint v0.7 事件（UserOperationEvent 用于回执匹配）
ENTRY_POINT_EVENTS_ABI: list[dict] = [
    {
        "name": "UserOperationEvent",
        "type": "event",
        "anonymous": False,
        "inputs": [
            {"name": "userOpHash", "type": "bytes32", "indexed": True},
            {"name": "sender", "type": "address", "indexed": True},
            {"name": "paymaster", "type": "address", "indexed": True},
            {"name": "nonce", "type": "uint256", "indexed": False},
            {"name": "success", "type": "bool", "indexed": False},
            {"name": "actualGasCost", "type": "uint256", "indexed": False},
            {"name": "actualGasUsed", "type": "uint256", "indexed": False},
        ],
    }
]

# EntryPoint v0.7 视图：UserOp 哈希（签名依赖）
ENTRY_POINT_ABI: list[dict] = [
    {
        "name": "getUserOpHash",
        "type": "function",
        "stateMutability": "view",
        "inputs": [
            {
                "name": "userOp",
                "components": [
                    {"name": "sender", "type": "address"},
                    {"name": "nonce", "type": "uint256"},
                    {"name": "initCode", "type": "bytes"},
                    {"name": "callData", "type": "bytes"},
                    {"name": "accountGasLimits", "type": "bytes32"},
                    {"name": "preVerificationGas", "type": "uint256"},
                    {"name": "gasFees", "type": "bytes32"},
                    {"name": "paymasterAndData", "type": "bytes"},
                    {"name": "signature", "type": "bytes"},
                ],
                "internalType": "struct UserOperation",
                "type": "tuple",
            }
        ],
        "outputs": [{"name": "", "type": "bytes32"}],
    }
]
