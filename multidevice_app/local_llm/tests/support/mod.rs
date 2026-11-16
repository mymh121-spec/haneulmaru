//! Tiny, untrained GGUF written from scratch for offline loader/generation tests.
//! It always prefers the ordinary token "x"; it cannot answer questions.
use std::{fs, path::Path};

fn string(out: &mut Vec<u8>, text: &str) {
    out.extend((text.len() as u64).to_le_bytes());
    out.extend(text.as_bytes());
}
fn entry(key: &str, ty: u32, bytes: &[u8]) -> Vec<u8> {
    let mut out = Vec::new();
    string(&mut out, key);
    out.extend(ty.to_le_bytes());
    out.extend(bytes);
    out
}
fn text(key: &str, value: &str) -> Vec<u8> {
    let mut bytes = Vec::new();
    string(&mut bytes, value);
    entry(key, 8, &bytes)
}
fn uint(key: &str, value: u32) -> Vec<u8> {
    entry(key, 4, &value.to_le_bytes())
}

pub fn tiny_gguf(path: &Path) {
    let mut tokens = vec![
        "x".to_owned(),
        "<unk>".into(),
        "</s>".into(),
        "<s>".into(),
        "▁".into(),
    ];
    tokens.extend((0..256).map(|b| format!("<0x{b:02X}>")));
    let vocab = tokens.len();
    let mut token_array = 8u32.to_le_bytes().to_vec();
    token_array.extend((vocab as u64).to_le_bytes());
    for token in &tokens {
        string(&mut token_array, token);
    }
    let mut scores = 6u32.to_le_bytes().to_vec();
    scores.extend((vocab as u64).to_le_bytes());
    for _ in &tokens {
        scores.extend(0f32.to_le_bytes());
    }
    let metadata = vec![
        text("general.architecture", "llama"),
        text("general.name", "haneulmaru-untrained-test-only"),
        uint("llama.embedding_length", 32),
        uint("llama.feed_forward_length", 64),
        uint("llama.attention.head_count", 1),
        uint("llama.attention.head_count_kv", 1),
        uint("llama.block_count", 1),
        uint("llama.rope.dimension_count", 32),
        uint("llama.context_length", 8192),
        entry(
            "llama.attention.layer_norm_rms_epsilon",
            6,
            &1e-5f32.to_le_bytes(),
        ),
        text("tokenizer.ggml.model", "llama"),
        entry("tokenizer.ggml.tokens", 9, &token_array),
        entry("tokenizer.ggml.scores", 9, &scores),
        uint("tokenizer.ggml.unknown_token_id", 1),
        uint("tokenizer.ggml.eos_token_id", 2),
        uint("tokenizer.ggml.bos_token_id", 3),
        text(
            "tokenizer.chat_template",
            "{% for message in messages %}{{ message['content'] }}\n{% endfor %}",
        ),
    ];
    // GGUF dimensions are innermost first. F32 tensor type = 0.
    let tensors: Vec<(&str, Vec<u64>, f32)> = vec![
        ("token_embd.weight", vec![32, vocab as u64], 1.),
        ("output_norm.weight", vec![32], 1.),
        ("output.weight", vec![32, vocab as u64], 0.),
        ("blk.0.attn_norm.weight", vec![32], 1.),
        ("blk.0.ffn_norm.weight", vec![32], 1.),
        ("blk.0.attn_q.weight", vec![32, 32], 0.),
        ("blk.0.attn_k.weight", vec![32, 32], 0.),
        ("blk.0.attn_v.weight", vec![32, 32], 0.),
        ("blk.0.attn_output.weight", vec![32, 32], 0.),
        ("blk.0.ffn_gate.weight", vec![32, 64], 0.),
        ("blk.0.ffn_up.weight", vec![32, 64], 0.),
        ("blk.0.ffn_down.weight", vec![64, 32], 0.),
    ];
    let mut out = b"GGUF".to_vec();
    out.extend(3u32.to_le_bytes());
    out.extend((tensors.len() as u64).to_le_bytes());
    out.extend((metadata.len() as u64).to_le_bytes());
    for m in metadata {
        out.extend(m);
    }
    let mut data = Vec::new();
    for (name, dims, value) in tensors {
        string(&mut out, name);
        out.extend((dims.len() as u32).to_le_bytes());
        for d in &dims {
            out.extend(d.to_le_bytes());
        }
        out.extend(0u32.to_le_bytes());
        out.extend((data.len() as u64).to_le_bytes());
        for i in 0..dims.iter().product::<u64>() {
            let v = if name == "output.weight" && i < 32 {
                1f32
            } else {
                value
            };
            data.extend(v.to_le_bytes());
        }
        while data.len() % 32 != 0 {
            data.push(0);
        }
    }
    while out.len() % 32 != 0 {
        out.push(0);
    }
    out.extend(data);
    fs::write(path, out).unwrap();
}
