//! Sandbox code execution tools
//!
//! Execute Python code and shell commands in a sandboxed environment.
//! Supports both local Docker and remote sandbox API.

use reqwest::Client;
use serde::{Deserialize, Serialize};
use serde_json::json;

#[derive(Debug, Serialize, Deserialize)]
struct SandboxResponse {
    success: bool,
    output: Option<String>,
    error: Option<String>,
}

pub struct SandboxTools {
    client: Client,
    api_url: Option<String>,
    api_key: Option<String>,
}

impl SandboxTools {
    pub fn new() -> Self {
        Self {
            client: Client::new(),
            api_url: std::env::var("SANDBOX_API_URL").ok(),
            api_key: std::env::var("SANDBOX_API_KEY").ok(),
        }
    }

    async fn call_sandbox(&self, endpoint: &str, body: serde_json::Value) -> Result<String, String> {
        let base_url = self.api_url.as_ref()
            .ok_or("SANDBOX_API_URL not configured")?;

        let url = format!("{}{}", base_url, endpoint);

        let mut request = self.client
            .post(&url)
            .header("Content-Type", "application/json")
            .json(&body);

        if let Some(key) = &self.api_key {
            request = request.header("X-API-Key", key);
        }

        let response = request
            .send()
            .await
            .map_err(|e| format!("Sandbox request failed: {}", e))?;

        if !response.status().is_success() {
            let status = response.status();
            let body = response.text().await.unwrap_or_default();
            return Err(format!("Sandbox API error {}: {}", status, body));
        }

        let result: SandboxResponse = response
            .json()
            .await
            .map_err(|e| format!("Failed to parse response: {}", e))?;

        if result.success {
            Ok(result.output.unwrap_or_default())
        } else {
            Err(result.error.unwrap_or_else(|| "Unknown error".to_string()))
        }
    }

    pub async fn execute_python(&self, code: String) -> Result<String, String> {
        self.call_sandbox("/execute", json!({
            "code": code,
            "language": "python"
        })).await
    }

    pub async fn install_package(&self, package: String) -> Result<String, String> {
        self.call_sandbox("/install", json!({
            "package": package
        })).await
    }

    pub async fn read_file(&self, path: String) -> Result<String, String> {
        self.call_sandbox("/files/read", json!({
            "path": path
        })).await
    }

    pub async fn write_file(&self, path: String, content: String) -> Result<String, String> {
        self.call_sandbox("/files/write", json!({
            "path": path,
            "content": content
        })).await
    }

    pub async fn list_files(&self, path: Option<String>) -> Result<String, String> {
        let dir = path.unwrap_or_else(|| "/home/user".to_string());
        self.call_sandbox("/files/list", json!({
            "path": dir
        })).await
    }

    pub async fn run_shell(&self, command: String) -> Result<String, String> {
        self.call_sandbox("/shell", json!({
            "command": command
        })).await
    }
}
