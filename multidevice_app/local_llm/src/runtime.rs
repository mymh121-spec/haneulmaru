//! Upstream SDK dependency; no fork, copied engine, Python process or HTTP wrapper.
use crate::{Answer, Request};
use anyhow::{Context, Result};
use haneulmaru::evidence::SYSTEM_PROMPT;
use mistralrs::{GgufModelBuilder, RequestBuilder, TextMessageRole, TokenSource};

pub fn generate(request: Request) -> Result<Answer> {
    tokio::runtime::Builder::new_multi_thread().enable_all().build()?.block_on(async move {
        let directory = request.model.parent().context("model directory missing")?;
        let name = request.model.file_name().and_then(|n|n.to_str()).context("model filename must be UTF-8")?;
        // Existing absolute directory and filename select the SDK's local GGUF path.
        // No tokenizer model ID, Hub token, search, MCP, tools or remote selector.
        let mut builder = GgufModelBuilder::new(directory.to_string_lossy(), vec![name])
            .with_token_source(TokenSource::None)
            .with_max_num_seqs(1).with_prefix_cache_n(None);
        if !request.accelerator { builder = builder.with_force_cpu(); }
        if let Some(template) = request.chat_template {
            builder = builder.with_chat_template(template.to_string_lossy());
        }
        let model = builder.build().await.context("load local GGUF; ensure its architecture and chat template are supported by mistralrs 0.8.1")?;
        let messages = RequestBuilder::new()
            .add_message(TextMessageRole::System, SYSTEM_PROMPT)
            .add_message(TextMessageRole::User, request.prompt)
            .enable_thinking(false).set_sampler_temperature(0.)
            .set_sampler_max_len(request.max_tokens as usize);
        let response = model.send_chat_request(messages).await?;
        let text = response.choices.first().and_then(|c|c.message.content.as_ref())
            .context("model returned no text choice")?.to_owned();
        Ok(Answer { text, prompt_tokens: response.usage.prompt_tokens,
            completion_tokens: response.usage.completion_tokens })
    })
}
