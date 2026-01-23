# Clara MCP Server

A native MCP (Model Context Protocol) server written in Rust that exposes Clara's core tools. This server runs as a stdio-based MCP server and integrates with Clara's Discord bot or any MCP-compatible client.

## Features

- **Claude Code Integration** - Execute coding tasks via Claude Code CLI
- **Sandbox Execution** - Run Python code and shell commands in isolated environments
- **ORS Notes** - Organic Response System note management

## Building

```bash
cd clara-mcp-server
cargo build --release
```

The binary will be at `target/release/clara-mcp-server`.

## Running

```bash
# Direct execution (stdio mode)
./target/release/clara-mcp-server

# With debug logging
RUST_LOG=debug ./target/release/clara-mcp-server
```

## Configuration

The server reads configuration from environment variables:

| Variable | Description |
|----------|-------------|
| `DATABASE_URL` | PostgreSQL connection string for ORS notes |
| `SANDBOX_API_URL` | Remote sandbox service URL |
| `SANDBOX_API_KEY` | Remote sandbox API key |

## Available Tools

### Claude Code (4 tools)
| Tool | Description |
|------|-------------|
| `claude_code` | Execute a coding task using Claude Code CLI |
| `claude_code_status` | Check Claude Code availability and status |
| `claude_code_get_workdir` | Get the current working directory |
| `claude_code_set_workdir` | Set the working directory |

### Sandbox (6 tools)
| Tool | Description |
|------|-------------|
| `execute_python` | Execute Python code in a sandboxed environment |
| `install_package` | Install a Python package in the sandbox |
| `sandbox_read_file` | Read a file from the sandbox |
| `sandbox_write_file` | Write content to a file in the sandbox |
| `sandbox_list_files` | List files in a sandbox directory |
| `run_shell` | Run a shell command in the sandbox |

### ORS Notes (3 tools)
| Tool | Description |
|------|-------------|
| `ors_list_notes` | List ORS notes for a user |
| `ors_add_note` | Add an ORS note |
| `ors_archive_note` | Archive an ORS note |

## Integration with Clara

Clara automatically registers this server on startup if the binary is found. The config is stored in `.mcp_servers/clara-tools/config.json`:

```json
{
  "name": "clara-tools",
  "source_type": "local",
  "transport": "stdio",
  "command": "/path/to/clara-mcp-server",
  "enabled": true
}
```

## Development

### Project Structure

```
clara-mcp-server/
├── Cargo.toml          # Dependencies and build config
├── src/
│   ├── main.rs         # Server entry point and tool definitions
│   └── tools/
│       ├── mod.rs      # Tool module exports
│       ├── claude_code.rs
│       ├── ors_notes.rs
│       └── sandbox.rs
└── target/
    └── release/
        └── clara-mcp-server
```

### Adding New Tools

1. Create a new module in `src/tools/`
2. Export it in `src/tools/mod.rs`
3. Add the tool struct to `ClaraServer` in `main.rs`
4. Implement tool methods with the `#[tool]` macro

Example:
```rust
#[tool(description = "My new tool")]
async fn my_tool(&self, Parameters(p): Parameters<MyParams>) -> Result<CallToolResult, McpError> {
    match self.my_module.do_something(p.arg).await {
        Ok(text) => Ok(CallToolResult::success(vec![Content::text(text)])),
        Err(e) => Ok(CallToolResult::error(vec![Content::text(e)])),
    }
}
```

### Dependencies

- `rmcp` - Rust MCP SDK
- `tokio` - Async runtime
- `reqwest` - HTTP client for API calls
- `sqlx` - Database access (PostgreSQL/SQLite)
- `serde` / `schemars` - Serialization and JSON schema generation

## License

MIT
