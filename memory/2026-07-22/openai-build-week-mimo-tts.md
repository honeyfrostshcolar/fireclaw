# OpenAI Build Week MiMo TTS

## 2026-07-22

- Task goal: replace the low-quality local voiceover in the isolated Build Week submission with Xiaomi MiMo V2.5 TTS.
- Submission directory: `openai-build-week-submission/`.
- Observed error: `video/generate_mimo_voice.py` returned HTTP 401 on the first segment.
- Root cause: the script sent the Claude Code Token Plan credential (`tp-` class) to the pay-as-you-go endpoint `https://api.xiaomimimo.com/v1`. Xiaomi credentials and endpoints are plan-specific.
- Modified file: `openai-build-week-submission/video/generate_mimo_voice.py`.
- Fix: resolve the API endpoint from `MIMO_BASE_URL` when provided; otherwise convert Claude's configured `/anthropic` Token Plan URL to `/v1/chat/completions` for `tp-` credentials; retain the pay-as-you-go endpoint for other credentials.
- Security: no credential value is printed or written to the submission directory.
- Verification: `python3 -m py_compile video/generate_mimo_voice.py` passed; `python3 video/generate_mimo_voice.py --dry-run` selected `https://token-plan-cn.xiaomimimo.com/v1/chat/completions` and found all 10 narration segments.
- Remaining step: run `python3 video/generate_mimo_voice.py` from `openai-build-week-submission/` in the user's normal terminal, which has network access. The script will generate ten WAV files and rebuild the final MP4 automatically.

## 2026-07-22 Completion Update

- MiMo generated all 10 English narration WAV files successfully using the `Dean` voice.
- Final video: `openai-build-week-submission/video/fireclaw-openai-build-week-demo.mp4`.
- Media validation with `gst-discoverer-1.0`: QuickTime/MP4 container, H.264 High Profile video, MPEG-4 AAC audio, seekable, duration `00:02:19.6225`.
- File size: 7,328,039 bytes. Narration duration reported by the generator: 139.58 seconds; final container duration differs by about 0.04 seconds.
- The final video is under the three-minute submission limit.
