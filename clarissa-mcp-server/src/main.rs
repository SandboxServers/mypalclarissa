//! Clara MCP Server
//!
//! Native tools for Clara exposed via the Model Context Protocol.
//! Run with: cargo run

mod tools;

use rmcp::{
    ErrorData as McpError,
    ServerHandler,
    ServiceExt,
    handler::server::{router::tool::ToolRouter, wrapper::Parameters},
    model::*,
    tool,
    tool_handler,
    tool_router,
    transport::stdio,
};
use serde::Deserialize;
use schemars::JsonSchema;
use std::sync::Arc;
use tracing_subscriber::{layer::SubscriberExt, util::SubscriberInitExt};

use crate::tools::{
    sandbox::SandboxTools,
    ors_notes::OrsNotesTools,
};

// ========== Parameter Types ==========

#[derive(Debug, Deserialize, JsonSchema)]
pub struct CodeParams {
    /// Python code to execute
    pub code: String,
}

#[derive(Debug, Deserialize, JsonSchema)]
pub struct PackageParams {
    /// Package name to install
    pub package: String,
}

#[derive(Debug, Deserialize, JsonSchema)]
pub struct PathParams {
    /// File or directory path
    pub path: String,
}

#[derive(Debug, Deserialize, JsonSchema)]
pub struct WriteFileParams {
    /// File path
    pub path: String,
    /// Content to write
    pub content: String,
}

#[derive(Debug, Deserialize, JsonSchema)]
pub struct ListFilesParams {
    /// Directory path (optional)
    pub path: Option<String>,
}

#[derive(Debug, Deserialize, JsonSchema)]
pub struct ShellParams {
    /// Shell command
    pub command: String,
}

#[derive(Debug, Deserialize, JsonSchema)]
pub struct UserIdParams {
    /// User ID
    pub user_id: String,
}

#[derive(Debug, Deserialize, JsonSchema)]
pub struct AddNoteParams {
    /// User ID
    pub user_id: String,
    /// Note content
    pub content: String,
    /// Category (optional)
    pub category: Option<String>,
}

#[derive(Debug, Deserialize, JsonSchema)]
pub struct NoteIdParams {
    /// Note ID
    pub note_id: String,
}

// ========== Server Implementation ==========

/// Clara MCP Server
#[derive(Clone)]
pub struct ClaraServer {
    sandbox: Arc<SandboxTools>,
    ors_notes: Arc<OrsNotesTools>,
    tool_router: ToolRouter<Self>,
}

#[tool_router]
impl ClaraServer {
    pub fn new() -> Self {
        Self {
            sandbox: Arc::new(SandboxTools::new()),
            ors_notes: Arc::new(OrsNotesTools::new()),
            tool_router: Self::tool_router(),
        }
    }

    // ===== Sandbox Tools =====

    #[tool(description = "Execute Python code in a sandboxed environment")]
    async fn execute_python(&self, Parameters(p): Parameters<CodeParams>) -> Result<CallToolResult, McpError> {
        match self.sandbox.execute_python(p.code).await {
            Ok(text) => Ok(CallToolResult::success(vec![Content::text(text)])),
            Err(e) => Ok(CallToolResult::error(vec![Content::text(e)])),
        }
    }

    #[tool(description = "Install a Python package in the sandbox")]
    async fn install_package(&self, Parameters(p): Parameters<PackageParams>) -> Result<CallToolResult, McpError> {
        match self.sandbox.install_package(p.package).await {
            Ok(text) => Ok(CallToolResult::success(vec![Content::text(text)])),
            Err(e) => Ok(CallToolResult::error(vec![Content::text(e)])),
        }
    }

    #[tool(description = "Read a file from the sandbox")]
    async fn sandbox_read_file(&self, Parameters(p): Parameters<PathParams>) -> Result<CallToolResult, McpError> {
        match self.sandbox.read_file(p.path).await {
            Ok(text) => Ok(CallToolResult::success(vec![Content::text(text)])),
            Err(e) => Ok(CallToolResult::error(vec![Content::text(e)])),
        }
    }

    #[tool(description = "Write content to a file in the sandbox")]
    async fn sandbox_write_file(&self, Parameters(p): Parameters<WriteFileParams>) -> Result<CallToolResult, McpError> {
        match self.sandbox.write_file(p.path, p.content).await {
            Ok(text) => Ok(CallToolResult::success(vec![Content::text(text)])),
            Err(e) => Ok(CallToolResult::error(vec![Content::text(e)])),
        }
    }

    #[tool(description = "List files in a sandbox directory")]
    async fn sandbox_list_files(&self, Parameters(p): Parameters<ListFilesParams>) -> Result<CallToolResult, McpError> {
        match self.sandbox.list_files(p.path).await {
            Ok(text) => Ok(CallToolResult::success(vec![Content::text(text)])),
            Err(e) => Ok(CallToolResult::error(vec![Content::text(e)])),
        }
    }

    #[tool(description = "Run a shell command in the sandbox")]
    async fn run_shell(&self, Parameters(p): Parameters<ShellParams>) -> Result<CallToolResult, McpError> {
        match self.sandbox.run_shell(p.command).await {
            Ok(text) => Ok(CallToolResult::success(vec![Content::text(text)])),
            Err(e) => Ok(CallToolResult::error(vec![Content::text(e)])),
        }
    }

    // ===== ORS Notes Tools =====

    #[tool(description = "List ORS notes for a user")]
    async fn ors_list_notes(&self, Parameters(p): Parameters<UserIdParams>) -> Result<CallToolResult, McpError> {
        match self.ors_notes.list(p.user_id).await {
            Ok(text) => Ok(CallToolResult::success(vec![Content::text(text)])),
            Err(e) => Ok(CallToolResult::error(vec![Content::text(e)])),
        }
    }

    #[tool(description = "Add an ORS note")]
    async fn ors_add_note(&self, Parameters(p): Parameters<AddNoteParams>) -> Result<CallToolResult, McpError> {
        match self.ors_notes.add(p.user_id, p.content, p.category).await {
            Ok(text) => Ok(CallToolResult::success(vec![Content::text(text)])),
            Err(e) => Ok(CallToolResult::error(vec![Content::text(e)])),
        }
    }

    #[tool(description = "Archive an ORS note")]
    async fn ors_archive_note(&self, Parameters(p): Parameters<NoteIdParams>) -> Result<CallToolResult, McpError> {
        match self.ors_notes.archive(p.note_id).await {
            Ok(text) => Ok(CallToolResult::success(vec![Content::text(text)])),
            Err(e) => Ok(CallToolResult::error(vec![Content::text(e)])),
        }
    }
}

#[tool_handler]
impl ServerHandler for ClaraServer {
    fn get_info(&self) -> ServerInfo {
        ServerInfo {
            protocol_version: ProtocolVersion::LATEST,
            capabilities: ServerCapabilities::builder().enable_tools().build(),
            server_info: Implementation::from_build_env(),
            instructions: Some("Clara's native tools for sandbox execution and notes.".into()),
        }
    }
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    // Initialize tracing
    tracing_subscriber::registry()
        .with(tracing_subscriber::fmt::layer().with_writer(std::io::stderr))
        .with(tracing_subscriber::EnvFilter::from_default_env())
        .init();

    // Load environment
    dotenvy::dotenv().ok();

    tracing::info!("Starting Clara MCP Server v{}", env!("CARGO_PKG_VERSION"));

    // Create and run server
    let server = ClaraServer::new();

    // Serve via stdio
    let service = server.serve(stdio()).await?;
    service.waiting().await?;

    Ok(())
}
