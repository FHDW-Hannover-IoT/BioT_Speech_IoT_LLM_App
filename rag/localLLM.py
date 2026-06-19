import os
import shutil
import time
from langchain_community.document_loaders import PyPDFLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_chroma import Chroma
from langchain_community.embeddings import OllamaEmbeddings
from langchain_community.llms import Ollama
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnablePassthrough
from langchain_core.output_parsers import StrOutputParser

DB_DIR = "./chroma_db_MAV2.3"

# HIER NEU: Das deutlich stärkere mehrsprachige/deutsche Embedding-Modell
embeddings = OllamaEmbeddings(model="nomic-embed-text")

if not os.path.exists(DB_DIR):
    print("Erstelle hochpräzise Datenbank aus PDF...")
    loader = PyPDFLoader("MainV2.3.pdf")  # Pfad prüfen!
    docs = loader.load()
    
    # HIER NEU: Größere Chunks (1000) und mehr Überlappung (200),
    # damit technische Zusammenhänge nicht zerrissen werden.
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=1000, 
        chunk_overlap=200,
        separators=["\n\n", "\n", ".", " ", ""] # Versucht erst bei Absätzen, dann bei Sätzen zu trennen
    )
    chunks = text_splitter.split_documents(docs)
    
    vector_store = Chroma.from_documents(chunks, embeddings, persist_directory=DB_DIR)
    print("DB erfolgreich gespeichert!\n")
else:
    print("Bestehende präzise Vektordatenbank geladen.\n")
    vector_store = Chroma(persist_directory=DB_DIR, embedding_function=embeddings)

# HIER NEU: Wir holen jetzt die Top 5 relevantesten Abschnitte (k=5) statt nur 3
retriever = vector_store.as_retriever(search_kwargs={"k": 5})

# Als Chat-Modell empfiehlt sich alternativ auch "mistral", falls llama3 zu kreativ wird
llm = Ollama(model="llama3", temperature=0.0) # temperature=0.0 verhindert, dass das Modell "erfindet"

def format_docs(docs):
    return "\n\n".join(doc.page_content for doc in docs)

system_prompt = (
    "You are a precise technical support assistant.\n"
    "Your task is to answer the user's question based ONLY on the provided English context.\n"
    "The context contains technical documentation and descriptions of diagrams.\n"
    "If you do not know the answer, state it clearly.\n\n"
    "Context:\n{context}"
)

prompt = ChatPromptTemplate.from_messages([
    ("system", system_prompt),
    ("human", "{question}"),
])

rag_chain = (
    {"context": retriever | format_docs, "question": RunnablePassthrough()}
    | prompt
    | llm
    | StrOutputParser()
)

print("--- Präziser Lokaler Handbuch-Chat bereit ---")

while True:
    timeElapsed = time.time()

    user_input = input("Deine Frage: ")
    if user_input.lower() in ['exit', 'quit']:
        break
    if not user_input.strip():
        timeElapsed = time.time()
        continue 
        
    print("\nSuche in Vektordatenbank und generiere Antwort...")
    antwort = rag_chain.invoke(user_input)
    
    print(time.time() - timeElapsed)
    print(f"\nAntwort:\n{antwort}")
    print("-" * 40 + "\n")