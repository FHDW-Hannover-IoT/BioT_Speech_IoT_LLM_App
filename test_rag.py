import os
from dotenv import load_dotenv
from mcp_server.sensor_mcp_server import query_rag

# Lade den API-Key aus der .env Datei
load_dotenv()

def run_test():
    print("Starte RAG-Test...")

    frage = "What does the manual say about the sensors used?"

    print(f"Frage: {frage}")

    antwort = query_rag(question=frage, top_k=3)

    print("\n--- ERGEBNIS ---")
    print(antwort)

if __name__ == "__main__":
    run_test()