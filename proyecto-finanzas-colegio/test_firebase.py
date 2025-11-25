import os
from firebase_admin import credentials, firestore, initialize_app
from dotenv import load_dotenv

load_dotenv()

cred = credentials.Certificate(os.getenv("FIREBASE_CREDS"))
initialize_app(cred)
db = firestore.client()

print("Conexión establecida correctamente con Firestore.")

# OPCIONAL: leer estudiantes si existen
try:
    students = list(db.collection("students").stream())
    print(f"Estudiantes encontrados: {len(students)}")
except:
    print("No se pudo leer estudiantes (es normal si aún no tienes ninguno).")
