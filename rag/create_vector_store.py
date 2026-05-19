import os
import time
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()
api_key = os.getenv("OPENAI_API_KEY")
if not api_key:
    raise RuntimeError("Missing OPENAI_API_KEY. Put it in a .env file or the environment.")
client = OpenAI(api_key=api_key)

def create_vector_store(store_name: str, file_id: str) -> None:
    vector_store = client.vector_stores.create(
        name=store_name
    )
    print(vector_store.id)

    result = client.vector_stores.files.create(
        vector_store_id=vector_store.id,
        file_id=file_id
    )
    print(result)

    time.sleep(8)  # wait a moment for processing
    result = client.vector_stores.files.list(
        vector_store_id=vector_store.id
    )
    print(result)

def delete_vector_store(store_id: str) -> None:
    result = client.vector_stores.delete(
        vector_store_id=store_id
    )
    print(result)

if __name__ == "__main__":
    create_vector_store(
        store_name="Integration Project BioMed IoT App Concept",
        file_id="file-84f4fFBidcTxV7YY58tMHS"
    ) # -> created vector store id: vs_69160490a5b08191a4ecd657e91a65ec