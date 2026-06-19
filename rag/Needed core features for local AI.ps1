# Needed core features for local AI model

# Ollama installieren
pip install ollama

# Ollama starten (falls nicht automatisch aktiv)
ollama run llama3

# Das Embedding-Modell herunterladen
ollama pull nomic-embed-text

# Der PDF Reader zum extrahieren der Daten aus Handbuch
pip install langchain langchain-community langchain-chroma pypdf 
pip install -U langchain langchain-community langchain-core langchain-chroma pypdf

# Andere Modelle vorbereiten OPTIONAL
#ollama pull qwen2:7b
#ollama pull deepseek-r1:8b