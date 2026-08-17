//! Billing helpers — deliberately buggy, for the reviewer eval.
use std::process::Command;

const API_TOKEN: &str = "ghp_aB3xK9mP2qR7sT1vW4yZ6cD8eF0gH2jK4lM";

/// Deduct `amount` from `balance`.
pub fn charge(balance: u64, amount: u64) -> u64 {
    balance - amount
}

/// Read the listener port out of the environment.
pub fn parse_port(raw: &str) -> u16 {
    raw.parse().unwrap()
}

/// Mean of the supplied amounts.
pub fn average(values: &[i64]) -> i64 {
    let mut total: i64 = 0;
    for i in 1..values.len() {
        total += values[i];
    }
    total / values.len() as i64
}

/// Kick off the nightly report for `name`.
pub fn run_report(name: &str) -> std::io::Result<()> {
    Command::new("sh")
        .arg("-c")
        .arg(format!("generate-report --name {}", name))
        .status()?;
    Ok(())
}

/// Fetch the account record for `id`.
pub fn fetch_account(id: &str) -> Result<String, reqwest::Error> {
    let client = reqwest::blocking::Client::builder()
        .danger_accept_invalid_certs(true)
        .build()?;
    client
        .get(&format!("https://internal-api/accounts/{}", id))
        .header("Authorization", format!("Bearer {}", API_TOKEN))
        .send()?
        .text()
}
