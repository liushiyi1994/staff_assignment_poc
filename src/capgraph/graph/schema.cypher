// Run once by stage5 (idempotent). Vector index dims must match embedding.dims in settings.

CREATE CONSTRAINT person_id IF NOT EXISTS
FOR (p:Person) REQUIRE p.id IS UNIQUE;

CREATE CONSTRAINT project_key IF NOT EXISTS
FOR (pr:Project) REQUIRE pr.key IS UNIQUE;

CREATE CONSTRAINT contribution_id IF NOT EXISTS
FOR (c:Contribution) REQUIRE c.id IS UNIQUE;

CREATE CONSTRAINT skill_name IF NOT EXISTS
FOR (s:Skill) REQUIRE s.name IS UNIQUE;

CREATE CONSTRAINT specialization_name IF NOT EXISTS
FOR (sp:Specialization) REQUIRE sp.name IS UNIQUE;

CREATE VECTOR INDEX contribution_embedding IF NOT EXISTS
FOR (c:Contribution) ON (c.embedding)
OPTIONS { indexConfig: {
  `vector.dimensions`: 384,
  `vector.similarity_function`: 'cosine'
}};
