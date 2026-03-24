const hre   = require("hardhat");
const fs    = require("fs");
const path  = require("path");

async function main() {
  // ── Signers ──────────────────────────────────────────────────────────────
  // accounts[0] = deployer / owner
  // accounts[1-3] = the three AI agent wallets registered on-chain
  const accounts = await hre.ethers.getSigners();
  const deployer = accounts[0];
  const agentAddresses = [
    accounts[1].address,
    accounts[2].address,
    accounts[3].address,
  ];

  console.log("──────────────────────────────────────────");
  console.log("Deploying DAOGovernance");
  console.log("  Network  :", hre.network.name);
  console.log("  Deployer :", deployer.address);
  console.log("──────────────────────────────────────────");

  // ── Deploy ───────────────────────────────────────────────────────────────
  const Factory = await hre.ethers.getContractFactory("DAOGovernance");
  const dao     = await Factory.deploy();
  await dao.waitForDeployment();

  const contractAddress = await dao.getAddress();
  console.log("✔ DAOGovernance deployed to:", contractAddress);

  // ── Register agents ──────────────────────────────────────────────────────
  console.log("\nRegistering agents…");
  for (let i = 0; i < agentAddresses.length; i++) {
    const tx = await dao.registerAgent(agentAddresses[i]);
    await tx.wait();
    console.log(`  ✔ Agent ${i + 1} registered: ${agentAddresses[i]}`);
  }

  const registeredCount = await dao.registeredAgentCount();
  console.log(`\n  Total registered agents: ${registeredCount}/3`);

  // ── Read ABI from compiled artifact ─────────────────────────────────────
  const artifactPath = path.join(
    __dirname,
    "..",
    "artifacts",
    "contracts",
    "DAOGovernance.sol",
    "DAOGovernance.json"
  );
  const artifact = JSON.parse(fs.readFileSync(artifactPath, "utf8"));

  // ── Write /agents/contract_info.json ────────────────────────────────────
  const outPath = path.join(__dirname, "..", "agents", "contract_info.json");
  const contractInfo = {
    network:         hre.network.name,
    contractAddress,
    deployedAt:      new Date().toISOString(),
    deployer:        deployer.address,
    agents: agentAddresses.map((addr, i) => ({
      index:   i + 1,
      address: addr,
    })),
    abi: artifact.abi,
  };

  fs.writeFileSync(outPath, JSON.stringify(contractInfo, null, 2));
  console.log("\n✔ contract_info.json written to:", outPath);
  console.log("──────────────────────────────────────────");
  console.log("Deployment complete.");
  console.log("──────────────────────────────────────────");
}

main().catch((err) => {
  console.error(err);
  process.exitCode = 1;
});
