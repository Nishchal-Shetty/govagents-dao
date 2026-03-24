require("@nomicfoundation/hardhat-toolbox");

/** @type import('hardhat/config').HardhatUserConfig */
module.exports = {
  solidity: "0.8.24",
  networks: {
    // In-process Hardhat Network (used by default for tests)
    hardhat: {},
    // Stand-alone node started with: npx hardhat node
    localhost: {
      url: "http://127.0.0.1:8545",
      chainId: 31337,
    },
  },
};
