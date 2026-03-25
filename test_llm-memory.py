from memory import ExtractionPipeline

pipeline = ExtractionPipeline()

# Ensure user and project exist
pipeline.repository.create_user("user_1")
pipeline.repository.create_project("user_1", "proj_1", "My Project")

# Run extraction
result = pipeline.run(
    text="Alice works at Google. She is learning Neo4j for graph databases.",
    user_id="user_1",
    project_id="proj_1"
)

print(result)
# {'success': True, 'extraction': {'entities': [...], 'relationships': [...]}, 'stats': {...}}

pipeline.close()