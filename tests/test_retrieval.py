from care_agent.retrieval import KnowledgeRetriever, tokenize


def test_tokenize_lowercases_and_drops_stopwords():
    tokens = tokenize("Should I take Supplements for the Cholesterol?")
    assert "should" not in tokens
    assert "the" not in tokens
    assert "for" not in tokens
    assert "supplements" in tokens
    assert "cholesterol" in tokens


def test_retrieve_ldl_query_surfaces_ldl_chunks(data_dir):
    retriever = KnowledgeRetriever(kb_path=data_dir / "knowledge_base.jsonl")
    results = retriever.retrieve("LDL cholesterol high what should I do", top_k=5)
    assert results, "expected at least one retrieved chunk"
    top_ids = {rc.chunk.id for rc in results}
    assert any(cid.startswith("kb_lipid_") for cid in top_ids)


def test_topic_filter_boosts_relevant_chunks(data_dir):
    retriever = KnowledgeRetriever(kb_path=data_dir / "knowledge_base.jsonl")
    results = retriever.retrieve("marker results", top_k=5, topic_filter={"supplements", "safety"})
    ids = [rc.chunk.id for rc in results]
    assert any(cid.startswith("kb_supplements_") for cid in ids)


def test_empty_query_returns_no_results(data_dir):
    retriever = KnowledgeRetriever(kb_path=data_dir / "knowledge_base.jsonl")
    assert retriever.retrieve("", top_k=5) == []


def test_get_by_id(data_dir):
    retriever = KnowledgeRetriever(kb_path=data_dir / "knowledge_base.jsonl")
    chunk = retriever.get_by_id("kb_a1c_005")
    assert chunk is not None
    assert "metabolic" in chunk.topic

    assert retriever.get_by_id("does_not_exist") is None


def test_all_chunks_loaded(data_dir):
    retriever = KnowledgeRetriever(kb_path=data_dir / "knowledge_base.jsonl")
    assert len(retriever.chunks) == 68
