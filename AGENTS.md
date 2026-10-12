# CreatorDNA development

- V1.0 implements folder input, transcription, Meta-Skill distillation, versioned Creator-Skills and new-topic script generation. Evaluation remains manual; do not add video production or a web application without a new requirement.
- Keep deterministic file/media/storage code separate from AI interpretation. Agent and ASR providers have explicit interfaces.
- Source material is read-only. Run artifacts, transcripts, drafts and failures belong in the configured workspace.
- For new videos, use dynamic, unedited, subtitle-free portrait references exclusively from `materials/<creator>/raw_portraits/`. For creator-001 this is `materials/creator-001/raw_portraits/`; the user adds originals manually. Edited parent-folder videos remain style-analysis references, never portrait-generation inputs. If originals are missing or unsuitable, stop and explain; do not fall back to edited clips or screenshots.
- Preserve the complete visible upper body and original body/hand motion, drive the mouth with the new speech, and lock one source file/person/outfit for the entire video. Small new body movements are permitted only through a real available generation capability and must be identified as generated; no static-photo or looping substitute for body motion.
- Creator-Skills must work independently of the original media, Meta-Skill and this Python package.
- Validate AI output against JSON Schema and check evidence against the actual transcripts before publishing a version.
- Do not represent fixture-based tests as evidence of real style quality. Keep synthetic examples explicitly labelled.
- Verify changes with `python -m unittest discover -s tests -v`. Update the Chinese README for user-visible CLI changes.
