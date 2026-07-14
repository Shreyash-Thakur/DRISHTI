from copilot.rag import Retriever


def test_retrieve_ranks_link_degradation_for_crc_query():
    retriever = Retriever.from_dir("data/runbooks")
    hits = retriever.retrieve("CRC input errors on the optic / dirty fiber", top_k=3)
    assert hits
    assert hits[0].runbook == "link-degradation"
    assert hits[0].score > 0


def test_retrieve_empty_for_no_signal_query():
    retriever = Retriever.from_dir("data/runbooks")
    assert retriever.retrieve("zzzzqqqq nonsense token", top_k=3) == []


def test_retrieve_is_deterministic():
    retriever = Retriever.from_dir("data/runbooks")
    a = retriever.retrieve("bgp keepalive hold timer adjacency flap", top_k=3)
    b = retriever.retrieve("bgp keepalive hold timer adjacency flap", top_k=3)
    assert [h.heading for h in a] == [h.heading for h in b]
    assert a[0].runbook == "bgp-flap"
