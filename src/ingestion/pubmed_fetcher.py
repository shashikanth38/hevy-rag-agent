from langchain_openai import OpenAIEmbeddings
from src.config import CHROMA_PATH, EMBEDDING_MODEL, OPENAI_API_KEY
from langchain_core.documents import Document
from langchain_chroma import Chroma
from Bio import Entrez
import time

Entrez.email = "g.shashikanth08@gmail.com"
SEARCH_QUERIES = [
    "resistance training volume hypertrophy sets per week",
    "progressive overload strength adaptation",
    "muscle recovery rest days training frequency",
    "deload week fatigue management strength training",
    "push pull legs training split effectiveness",
    "barbell compound movements strength gains",
    "RPE rate perceived exertion training load management",
    "protein synthesis muscle repair exercise",
    "smith machine barbell squat muscle activation",
    "weighted dips pull ups strength progression",
    "bicep curl tricep extension hypertrophy volume",
    "leg press squat hamstring quadriceps training",
    "overtraining syndrome recovery indicators",
    "training frequency muscle group weekly volume",
    "fatigue accumulation resistance training performance",
]

def fetch_abstracts(query: str, max_results: int = 8) -> list[dict]:
    """Fetch PubMed abstracts for a given search query."""
    try:
        handle  = Entrez.esearch(db="pubmed", term=query, retmax=max_results)
        record  = Entrez.read(handle)
        ids     = record["IdList"]
        handle.close()

        if not ids:
            print(f"  No results for: {query}")
            return []

        handle  = Entrez.efetch(
            db="pubmed", id=ids, rettype="abstract", retmode="xml"
        )
        records = Entrez.read(handle)
        handle.close()

        abstracts = []
        for article in records["PubmedArticle"]:
            try:
                medline  = article["MedlineCitation"]
                art      = medline["Article"]
                title    = str(art["ArticleTitle"])
                abstract_texts = art.get("Abstract", {}).get("AbstractText", [])
                abstract = " ".join(str(t) for t in abstract_texts)
                pmid     = str(medline["PMID"])

                if abstract.strip():
                    abstracts.append({
                        "pmid":     pmid,
                        "title":    title,
                        "abstract": abstract,
                        "query":    query,
                        "source":   f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                    })
            except (KeyError, IndexError):
                continue

        return abstracts

    except Exception as e:
        print(f"  Error fetching '{query}': {e}")
        return []
def build_documents(abstracts: list[dict]) -> list[Document]:
    """Convert raw abstracts into LangChain Document objects."""
    docs = []
    for item in abstracts:
        doc = Document(
            page_content = f"{item['title']}\n\n{item['abstract']}",
            metadata     = {
                "pmid":   item["pmid"],
                "title":  item["title"],
                "query":  item["query"],
                "source": item["source"],
            },
        )
        docs.append(doc)
    return docs
def run() -> None:
    print("setting up chroma db")
    embeddings = OpenAIEmbeddings(
        model=EMBEDDING_MODEL,
        openai_api_key=OPENAI_API_KEY
    )
    vector_store = Chroma(
        collection_name    = "fitness_papers",
        embedding_function = embeddings,
        persist_directory  = CHROMA_PATH,
    )
    all_docs = []
    for query in SEARCH_QUERIES:
        print(f"Fetching: {query}")
        abstracts = fetch_abstracts(query, max_results=8)
        docs      = build_documents(abstracts)
        all_docs.extend(docs)
        print(f"  → {len(docs)} abstracts")
        time.sleep(0.5)  # be polite to NCBI
# deduplicate by PMID before inserting
    seen     = set()
    unique   = []
    for doc in all_docs:
        pmid = doc.metadata["pmid"]
        if pmid not in seen:
            seen.add(pmid)
            unique.append(doc)

    print(f"\nTotal unique abstracts: {len(unique)}")
    vector_store.add_documents(unique)
    print(f"Stored in ChromaDB at: {CHROMA_PATH}")

    # quick smoke test
    results = vector_store.similarity_search(
        "how many sets per week for muscle growth?",
        k = 3,
    )
    print("\nSmoke test — top 3 results:")
    for r in results:
        print(f"  - {r.metadata['title'][:80]}")


if __name__ == "__main__":
    run()