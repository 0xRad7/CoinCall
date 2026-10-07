// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

/// @title MockUSDT — 测试与测试网冒烟用 ERC-20（对齐 BOT Chain USDT 的 6 位小数口径）。
/// @dev 开放 mint 仅供单测（eth-tester）与测试网 968 冒烟；绝不部署到主网 677。
///      transferFrom 语义与 Tether 系一致：无限授权(type(uint256).max)不减额度。
contract MockUSDT {
    string public constant name = "Mock USDT";
    string public constant symbol = "USDT";
    uint8 public constant decimals = 6;

    event Transfer(address indexed from, address indexed to, uint256 value);
    event Approval(address indexed owner, address indexed spender, uint256 value);

    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;

    function mint(address to, uint256 amount) external {
        balanceOf[to] += amount;
        emit Transfer(address(0), to, amount);
    }

    function approve(address spender, uint256 value) external returns (bool) {
        allowance[msg.sender][spender] = value;
        emit Approval(msg.sender, spender, value);
        return true;
    }

    function transfer(address to, uint256 value) external returns (bool) {
        return _transfer(msg.sender, to, value);
    }

    function transferFrom(address from, address to, uint256 value) external returns (bool) {
        uint256 allowed = allowance[from][msg.sender];
        require(allowed >= value, "insufficient allowance");
        if (allowed != type(uint256).max) {
            allowance[from][msg.sender] = allowed - value;
        }
        return _transfer(from, to, value);
    }

    function _transfer(address from, address to, uint256 value) private returns (bool) {
        require(balanceOf[from] >= value, "insufficient balance");
        balanceOf[from] -= value;
        balanceOf[to] += value;
        emit Transfer(from, to, value);
        return true;
    }
}
