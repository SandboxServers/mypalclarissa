//! ORS (Organic Response System) notes tools
//!
//! Manage observations and notes for proactive conversations.

use reqwest::Client;
use serde_json::json;

pub struct OrsNotesTools {
    client: Client,
    api_base: String,
}

impl OrsNotesTools {
    pub fn new() -> Self {
        let api_base = std::env::var("CLARA_API_URL")
            .unwrap_or_else(|_| "http://localhost:8000".to_string());

        Self {
            client: Client::new(),
            api_base,
        }
    }

    pub async fn list(&self, user_id: String) -> Result<String, String> {
        let url = format!("{}/ors/notes/{}", self.api_base, user_id);

        let response = self.client
            .get(&url)
            .send()
            .await
            .map_err(|e| format!("Failed to list notes: {}", e))?;

        if response.status().is_success() {
            let body = response.text().await.map_err(|e| e.to_string())?;
            Ok(body)
        } else {
            Ok("No active notes found.".to_string())
        }
    }

    pub async fn add(
        &self,
        user_id: String,
        content: String,
        category: Option<String>,
    ) -> Result<String, String> {
        let url = format!("{}/ors/notes", self.api_base);

        let body = json!({
            "user_id": user_id,
            "content": content,
            "category": category.unwrap_or_else(|| "general".to_string())
        });

        let response = self.client
            .post(&url)
            .json(&body)
            .send()
            .await
            .map_err(|e| format!("Failed to add note: {}", e))?;

        if response.status().is_success() {
            Ok("Note added successfully.".to_string())
        } else {
            let body = response.text().await.unwrap_or_default();
            Err(format!("Failed to add note: {}", body))
        }
    }

    pub async fn archive(&self, note_id: String) -> Result<String, String> {
        let url = format!("{}/ors/notes/{}/archive", self.api_base, note_id);

        let response = self.client
            .post(&url)
            .send()
            .await
            .map_err(|e| format!("Failed to archive note: {}", e))?;

        if response.status().is_success() {
            Ok("Note archived.".to_string())
        } else {
            let body = response.text().await.unwrap_or_default();
            Err(format!("Failed to archive note: {}", body))
        }
    }
}
