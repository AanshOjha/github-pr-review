import os
from openai import OpenAI
from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient
from azure.search.documents.models import VectorizedQuery
from azure.identity import (
    DefaultAzureCredential,
    get_bearer_token_provider,
)
from dotenv import load_dotenv

# Load environment variables from a .env file (optional)
load_dotenv()

# --- Configuration ---
# Set these as environment variables or replace them with your actual keys
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
ENDPOINT = os.environ.get("FOUNDRY_ENDPOINT")
DEPLOYMENT = os.environ.get("FOUNDRY_DEPLOYMENT")
AZURE_SEARCH_ENDPOINT = os.environ.get("AZURE_SEARCH_ENDPOINT")
AZURE_SEARCH_KEY = os.environ.get("AZURE_SEARCH_KEY")
AZURE_SEARCH_INDEX_NAME = os.environ.get("AZURE_SEARCH_INDEX")

# --- Initialize Clients ---
credential = DefaultAzureCredential()

token_provider = get_bearer_token_provider(
    credential,
    "https://ai.azure.com/.default",
)
# LLM
openai_LLM = OpenAI(
        base_url=ENDPOINT,
        api_key=token_provider
    )
openai_client = OpenAI(
        base_url=ENDPOINT,
        api_key=token_provider
    )
token = get_bearer_token_provider(
    credential,
    "https://search.azure.com/.default",
)
# Initialize Azure AI Search client
search_client = SearchClient(
    endpoint=AZURE_SEARCH_ENDPOINT,
    index_name=AZURE_SEARCH_INDEX_NAME,
    #credential=AzureKeyCredential(AZURE_SEARCH_KEY)
    credential=credential
)

def get_embedding(text: str, model: str = "text-embedding-3-small") -> list[float]:
    """Generates a vector embedding for the user's query using OpenAI."""
    response = openai_client.embeddings.create(input=[text], model=model)
    return response.data[0].embedding

def retrieve_documents(
    query: str,
    vector_field_name: str = "text_vector",
    top_k: int = 3
) -> list[str]:
    """
    Searches Azure AI Search for the most relevant documents
    based on the query vector.
    """

    # Generate embedding for the query
    query_vector = get_embedding(query)

    # Create vector search query
    vector_query = VectorizedQuery(
        vector=query_vector,
        k_nearest_neighbors=top_k,
        fields=vector_field_name
    )

    # Execute search
    search_results = search_client.search(
        search_text=None,
        vector_queries=[vector_query],
        select=["chunk"]
    )

    # Materialize results into a list
    results = list(search_results)

    print(
        f"\033[90m[Retrieved {len(results)} relevant documents from Azure AI Search.]\033[0m"
    )
    print("-" * 60)

    # Debug output
    if not results:
        print("No search results found.")
    else:
        print("Raw search results:")
        for i, doc in enumerate(results, start=1):
            print(f"\nResult #{i}")
            print(doc)

    # Extract document text
    docs = []

    for doc in results:
        chunk = doc.get("chunk")
        if chunk:
            docs.append(chunk)

    return docs

def chat_rag():
    """Main loop for the terminal chat application."""
    print("==================================================")
    print("🤖 Welcome to the Terminal RAG Chat!")
    print("Type 'exit' or 'quit' to end the session.")
    print("==================================================\n")
    
    # Initialize the conversation history with a system prompt
    chat_history = [
        {
            "role": "system", 
            "content": "You are a helpful assistant. Use the provided context to answer the user's question accurately. If the answer cannot be found in the context, explicitly state that you don't know based on the retrieved data.Do include Document title without fail, like \"Source\": \"Flexi Basket Scheme_Coforge.pdf\""
        }
    ]

    while True:
        user_input = input("\033[94mYou:\033[0m ") # Blue color for user input
        
        if user_input.strip().lower() in ['exit', 'quit']:
            print("Goodbye!")
            break
        if not user_input.strip():
            continue
            
        print("\033[90m[Searching Azure AI Search for context...]\033[0m")
        try:
            # 1. Retrieve relevant context
            context_docs = retrieve_documents(user_input)
            context_text = "\n\n".join(context_docs)
            
            # 2. Construct the prompt with the retrieved context
            augmented_prompt = f"Context:\n{context_text}\n\nQuestion: {user_input}"
            
            # Create a temporary message list for this specific turn
            messages = chat_history + [{"role": "user", "content": augmented_prompt}]
            
            print("\033[90m[Generating response via OpenAI...]\033[0m")
            
            # 3. Generate the response
            response = openai_LLM.chat.completions.create(
                model=DEPLOYMENT, # Change to gpt-3.5-turbo or another model if preferred
                messages=messages
            )
            print(f"\033[90m[Received response from OpenAI.]\033[0m")
            print("-" * 60)
            print("Raw OpenAI response:")
            #print(response)
            print("-" * 60)
            print(f"\033[90m[Input Tokens: {response.usage.prompt_tokens}]\033[0m")
            print(f"\033[90m[Output Tokens: {response.usage.completion_tokens}]\033[0m")

            answer = response.choices[0].message.content
            print(f"\n\033[92mAssistant:\033[0m {answer}\n") # Green color for assistant
            print("-" * 60)
            
            # 4. Save to history (saving the original question without context keeps the chat memory clean)
            chat_history.append({"role": "user", "content": user_input})
            chat_history.append({"role": "assistant", "content": answer})
            
        except Exception as e:
            print(f"\n\033[91mAn error occurred:\033[0m {e}\n")

if __name__ == "__main__":
    chat_rag()