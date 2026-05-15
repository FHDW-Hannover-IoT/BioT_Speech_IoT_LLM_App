import os
import requests
from dotenv import load_dotenv
from io import BytesIO
from openai import OpenAI

load_dotenv()
api_key = os.getenv("OPENAI_API_KEY")
if not api_key:
    raise RuntimeError("Missing OPENAI_API_KEY. Put it in a .env file or the environment.")
client = OpenAI(api_key=api_key)

def create_file(file_path: str) -> str:
    result = []
    if file_path.startswith("http://") or file_path.startswith("https://"):
        # Download the file content from the URL
        response = requests.get(file_path)
        file_content = BytesIO(response.content)
        file_name = file_path.split("/")[-1]
        file_tuple = (file_name, file_content)
        result = client.files.create(
            file=file_tuple,
            purpose="assistants"
        )
    else:
        # Handle local file path
        with open(file_path, "rb") as file_content:
            result = client.files.create(
                file=file_content,
                purpose="assistants"
            )
    print(result.id)
    return result.id

def delete_file(file_id_to_delete: str) -> None:
    result = client.files.delete(
        file_id=file_id_to_delete
    )
    print(result)

if __name__ == "__main__":
    # Replace with your own file path or URL
    file_id = create_file(r"C:\Users\mikeg\chatgpt_proxy\rag\BioT_Iot_AppKonzept_c2q3.pdf") # -> file id: file-84f4fFBidcTxV7YY58tMHS