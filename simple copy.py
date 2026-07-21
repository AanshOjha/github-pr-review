import os
import logging
from datetime import datetime
from openai import OpenAI
from azure.search.documents import SearchClient
from azure.search.documents.models import VectorizedQuery
from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# ---------------- Logging Setup ----------------
LOG_FILE = "app.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)

# ---------------- Config ----------------
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
ENDPOINT = os.environ.get("FOUNDRY_ENDPOINT")
DEPLOYMENT = os.environ.get("FOUNDRY_DEPLOYMENT")

AZURE_SEARCH_ENDPOINT = os.environ.get("AZURE_SEARCH_ENDPOINT")
AZURE_SEARCH_KEY = os.environ.get("AZURE_SEARCH_KEY")
AZURE_SEARCH_INDEX_NAME = os.environ.get("AZURE_SEARCH_INDEX")

# ---------------- Clients ----------------
try:
    credential = DefaultAzureCredential()

    token_provider = get_bearer_token_provider(
        credential,
        "https://ai.azure.com/.default",
    )

    openai_LLM = OpenAI(
        base_url=ENDPOINT,
        api_key=token_provider
    )

    openai_client = OpenAI(
        base_url=ENDPOINT,
        api_key=token_provider
    )

    search_client = SearchClient(
        endpoint=AZURE_SEARCH_ENDPOINT,
        index_name=AZURE_SEARCH_INDEX_NAME,
        credential=credential
    )

    logger.info("Successfully initialized OpenAI and Azure Search clients")

except Exception:
    logger.exception("Failed to initialize clients")
    raise


# ---------------- Embeddings ----------------
def get_embedding(text: str, model: str = "text-embedding-3-small") -> list[float]:
    try:
        response = openai_client.embeddings.create(input=[text], model=model)
        logger.info("Embedding generated successfully")
        return response.data[0].embedding
    except Exception:
        logger.exception("Embedding generation failed")
        raise


# ---------------- Retrieval ----------------
def retrieve_documents(query: str, vector_field_name: str = "text_vector", top_k: int = 3) -> list[str]:
    try:
        logger.info(f"Search query received: {query}")

        query_vector = get_embedding(query)

        vector_query = VectorizedQuery(
            vector=query_vector,
            k_nearest_neighbors=top_k,
            fields=vector_field_name
        )

        results = list(
            search_client.search(
                search_text=None,
                vector_queries=[vector_query],
                select=["chunk"]
            )
        )

        logger.info(f"Azure Search returned {len(results)} results")

        docs = []
        for doc in results:
            chunk = doc.get("chunk")
            if chunk:
                docs.append(chunk)

        # Log truncated docs for debugging
        logger.info(
            "Retrieved chunks: " +
            str([d[:300] for d in docs])  # truncate for log safety
        )

        return docs

    except Exception:
        logger.exception("Azure Search retrieval failed")
        raise


# ---------------- Chat RAG ----------------
def chat_rag():
    print("==================================================")
    print("🤖 Terminal RAG Chat (type exit/quit)")
    print("==================================================\n")

    chat_history = [
        {
            "role": "system",
            "content": (
                "You are a helpful assistant. Use provided context. "
                "If unknown, say you don't know. "
                "Always include document source."
            )
        }
    ]

    while True:
        user_input = input("You: ").strip()

        if user_input.lower() in ["exit", "quit"]:
            logger.info("User ended session")
            print("Goodbye!")
            break

        if not user_input:
            continue

        logger.info(f"User query: {user_input}")

        try:
            # ---------------- Retrieval ----------------
            context_docs = retrieve_documents(user_input)
            context_text = "\n\n".join(context_docs)

            augmented_prompt = f"Context:\n{context_text}\n\nQuestion: {user_input}"

            messages = chat_history + [
                {"role": "user", "content": augmented_prompt}
            ]

            # ---------------- LLM Call ----------------
            logger.info("Calling OpenAI LLM")

            response = openai_LLM.chat.completions.create(
                model=DEPLOYMENT,
                messages=messages
            )

            answer = response.choices[0].message.content

            usage = response.usage
            logger.info(
                f"LLM Response received | prompt_tokens={usage.prompt_tokens}, "
                f"completion_tokens={usage.completion_tokens}, total_tokens={usage.total_tokens}"
            )

            logger.info(f"Assistant response: {answer}")

            print(f"\nAssistant: {answer}\n")

            # ---------------- Memory Update ----------------
            chat_history.append({"role": "user", "content": user_input})
            chat_history.append({"role": "assistant", "content": answer})

        except Exception as e:
            logger.exception(f"Error processing query: {user_input}")
            print(f"\nError occurred: {e}\n")


# ---------------- Entry ----------------
if __name__ == "__main__":
    chat_rag()