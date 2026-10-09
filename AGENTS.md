# CreatorDNA development

- V1.0 implements folder input, transcription, Meta-Skill distillation, versioned Creator-Skills and new-topic script generation. Evaluation remains manual; do not add video production or a web application without a new requirement.
- Keep deterministic file/media/storage code separate from AI interpretation. Agent and ASR providers have explicit interfaces.
- Source material is read-only. Run artifacts, transcripts, drafts and failures belong in the configured workspace.
- Creator-Skills must work independently of the original media, Meta-Skill and this Python package.
- Validate AI output against JSON Schema and check evidence against the actual transcripts before publishing a version.
- Do not represent fixture-based tests as evidence of real style quality. Keep synthetic examples explicitly labelled.
- Verify changes with `python -m unittest discover -s tests -v`. Update the Chinese README for user-visible CLI changes.
