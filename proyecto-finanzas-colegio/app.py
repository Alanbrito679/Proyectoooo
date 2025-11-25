# app.py
import os
import io
from datetime import datetime
from zoneinfo import ZoneInfo
from flask import (
    Flask, request, jsonify, render_template,
    redirect, url_for, session, send_file
)
import firebase_admin
from firebase_admin import credentials, firestore
from dotenv import load_dotenv

# ============================
# CONFIG
# ============================
load_dotenv()
TZ = os.getenv("TZ", "America/La_Paz")

cred_path = os.getenv("FIREBASE_CREDS")
if not cred_path:
    raise RuntimeError("FIREBASE_CREDS not set in .env")

cred = credentials.Certificate(cred_path)
firebase_admin.initialize_app(cred)
db = firestore.client()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "SECRETO123")

# Ajusta según tu cuota mensual real
MONTHLY_FEE = int(os.getenv("MONTHLY_FEE", 500))


# ============================
# Util: cargar usuarios desde .env (ADMINS="user:pass,user2:pass2")
# ============================
def cargar_usuarios():
    raw = os.getenv("ADMINS", "")
    users = {}
    pares = [p.strip() for p in raw.split(",") if p.strip()]
    for p in pares:
        if ":" in p:
            u, pw = p.split(":", 1)
            users[u.strip()] = pw.strip()
    return users


USERS = cargar_usuarios()


# ============================
# Context processor: año actual para templates
# ============================
@app.context_processor
def inject_now_year():
    return {"now_year": datetime.now(ZoneInfo(TZ)).year}


# ============================
# LOGIN (root -> login)
# ============================
@app.route("/")
def raiz():
    # la primera pantalla debe ser login
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    year = datetime.now(ZoneInfo(TZ)).year
    if request.method == "POST":
        user = request.form.get("user", "").strip()
        pwd = request.form.get("password", "").strip()
        if user in USERS and USERS[user] == pwd:
            session["user"] = user
            return redirect(url_for("dashboard"))
        return render_template("login.html", error="Usuario o contraseña incorrectos", now_year=year)
    return render_template("login.html", now_year=year)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


def require_login():
    if "user" not in session:
        return redirect(url_for("login"))
    return None


# ============================
# DASHBOARD
# ============================
@app.route("/dashboard")
def dashboard():
    resp = require_login()
    if resp:
        return resp
    return render_template("index.html")


# ============================
# ESTUDIANTES
# ============================
@app.route("/students")
def students_page():
    resp = require_login()
    if resp:
        return resp
    return render_template("students.html")


@app.route("/api/students")
def api_students():
    resp = require_login()
    if resp:
        return resp

    curso = request.args.get("curso")
    paralelo = request.args.get("paralelo")
    year = datetime.now(ZoneInfo(TZ)).year

    if not curso or not paralelo:
        return jsonify({"error": "Faltan parámetros 'curso' y 'paralelo'"}), 400

    docs = db.collection("students") \
        .where("curso", "==", curso) \
        .where("paralelo", "==", paralelo).stream()

    lista = []
    for d in docs:
        s = d.to_dict()
        ci = s.get("ci")
        pago = db.collection("payments") \
            .where("student_ci", "==", ci) \
            .where("year", "==", year) \
            .where("month", "==", datetime.now(ZoneInfo(TZ)).month).stream()
        pagado = any(True for _ in pago)
        s["estado_mes_actual"] = "PAGO" if pagado else "NO"
        lista.append(s)

    return jsonify(lista)


# ============================
# ADD STUDENT
# ============================
@app.route("/add_student")
def add_student_page():
    resp = require_login()
    if resp:
        return resp
    return render_template("add_student.html")


@app.route("/api/add_student", methods=["POST"])
def api_add_student():
    resp = require_login()
    if resp:
        return resp

    data = request.json or {}
    required = ["ci", "first_name", "last_name_p", "last_name_m", "padre_tutor",
                "telefono", "curso", "paralelo", "anio_inscripcion"]
    for r in required:
        if r not in data or str(data[r]).strip() == "":
            return jsonify({"error": f"Campo obligatorio: {r}"}), 400

    ci = str(data["ci"]).strip()
    # Guardar estudiante
    db.collection("students").document(ci).set({
        "ci": ci,
        "first_name": data["first_name"].strip(),
        "last_name_p": data["last_name_p"].strip(),
        "last_name_m": data["last_name_m"].strip(),
        "padre_tutor": data["padre_tutor"].strip(),
        "telefono": data["telefono"].strip(),
        "curso": data["curso"].strip(),
        "paralelo": data["paralelo"].strip(),
        "anio_inscripcion": int(data["anio_inscripcion"]),
        "created_at": firestore.SERVER_TIMESTAMP
    })

    return jsonify({"msg": "Estudiante registrado correctamente"})


# ============================
# PAGOS
# ============================
@app.route("/register_payment")
def register_payment_page():
    resp = require_login()
    if resp:
        return resp
    return render_template("register_payment.html")


@app.route("/api/get_student_by_ci")
def api_get_student_by_ci():
    ci = request.args.get("ci")
    if not ci:
        return jsonify({"error": "Falta ci"}), 400
    doc = db.collection("students").document(ci).get()
    if not doc.exists:
        return jsonify({"error": "No existe"})
    return jsonify(doc.to_dict())


@app.route("/api/payments_by_year")
def api_payments_by_year():
    ci = request.args.get("ci")
    if not ci:
        return jsonify({"meses_pagados": []})
    try:
        year = int(request.args.get("year", datetime.now(ZoneInfo(TZ)).year))
    except:
        year = datetime.now(ZoneInfo(TZ)).year

    pagos = db.collection("payments") \
        .where("student_ci", "==", ci) \
        .where("year", "==", year).stream()

    meses = [p.to_dict().get("month") for p in pagos]
    return jsonify({"meses_pagados": meses})


@app.route("/api/register_payment", methods=["POST"])
def api_register_payment():
    data = request.json or {}
    ci = data.get("ci")
    if not ci:
        return jsonify({"error": "ci requerido"}), 400
    year = int(data.get("year", datetime.now(ZoneInfo(TZ)).year))
    months = data.get("months", [])
    if not isinstance(months, list):
        return jsonify({"error": "months debe ser lista"}), 400

    # Obtener estudiante (para curso/paralelo)
    doc = db.collection("students").document(ci).get()
    if not doc.exists:
        return jsonify({"error": "Estudiante no encontrado"}), 404
    est = doc.to_dict()
    curso = est.get("curso", "Desconocido")
    paralelo = est.get("paralelo", "")

    confirmados = []
    for m in months:
        try:
            mm = int(m)
        except:
            continue
        # verificar duplicados
        q = db.collection("payments") \
            .where("student_ci", "==", ci) \
            .where("year", "==", year) \
            .where("month", "==", mm).stream()
        if any(True for _ in q):
            continue
        db.collection("payments").add({
            "student_ci": ci,
            "curso": curso,
            "paralelo": paralelo,
            "month": mm,
            "year": year,
            "amount": MONTHLY_FEE,
            "paid_at": firestore.SERVER_TIMESTAMP
        })
        confirmados.append(mm)
    return jsonify({"registrados": confirmados})


# ============================
# REPORTES JSON
# ============================
@app.route("/report")
def report_page():
    resp = require_login()
    if resp:
        return resp
    return render_template("report.html")


@app.route("/api/report/annual")
def api_report_annual():
    year = int(request.args.get("year", datetime.now(ZoneInfo(TZ)).year))

    # Recolectar students map ci -> (curso, paralelo)
    students_docs = db.collection("students").stream()
    students_map = {}  # ci -> {curso, paralelo}
    for sdoc in students_docs:
        s = sdoc.to_dict()
        ci = s.get("ci")
        if ci:
            students_map[ci] = {
                "curso": s.get("curso", "Desconocido"),
                "paralelo": s.get("paralelo", "")
            }

    pagos = db.collection("payments").where("year", "==", year).stream()

    total = 0
    por_paralelo = {}
    por_mes = {i: 0 for i in range(1, 13)}
    pagos_por_clave_mes = {}  # {clave: {mes: set(ci)}}
    pagos_amount_por_clave_mes = {}  # {clave: {mes: sum_amount}}
    estudiantes_por_clave = {}  # {clave: set(ci)}

    # llenar estudiantes_por_clave desde students_map
    for ci, info in students_map.items():
        clave = f"{info.get('curso','Desconocido')} {info.get('paralelo','')}".strip()
        estudiantes_por_clave.setdefault(clave, set()).add(ci)

    # Recolectar pagos reales
    for pdoc in pagos:
        d = pdoc.to_dict()
        ci = d.get("student_ci")
        monto = float(d.get("amount", 0))
        mes = int(d.get("month", 0)) if d.get("month") else 0

        # determinar clave: preferir datos del pago si existen, sino del student map
        curso = d.get("curso")
        paralelo = d.get("paralelo")
        if not curso:
            # intentar del estudiante
            st = students_map.get(ci)
            if st:
                curso = st.get("curso")
                paralelo = st.get("paralelo")
        if not curso:
            curso = "Desconocido"
            paralelo = ""

        clave = f"{curso} {paralelo}".strip()

        total += monto
        por_paralelo[clave] = por_paralelo.get(clave, 0) + monto
        por_mes[mes] = por_mes.get(mes, 0) + monto

        pagos_por_clave_mes.setdefault(clave, {}).setdefault(mes, set()).add(ci)
        pagos_amount_por_clave_mes.setdefault(clave, {}).setdefault(mes, 0.0)
        pagos_amount_por_clave_mes[clave][mes] += monto

        # asegurar que el estudiante esté contado bajo la clave
        if ci:
            estudiantes_por_clave.setdefault(clave, set()).add(ci)

    # construir detalle final con estructura rica
    detalle_extendido = {}
    for clave, monto in por_paralelo.items():
        cnt_students = len(estudiantes_por_clave.get(clave, set()))
        months_struct = {}
        for m in range(1, 13):
            paid_n = len(pagos_por_clave_mes.get(clave, {}).get(m, set()))
            paid_amount = pagos_amount_por_clave_mes.get(clave, {}).get(m, 0.0)
            months_struct[m] = {
                "paid_students_count": paid_n,
                "paid_amount": paid_amount,
                "not_paid_count": max(0, cnt_students - paid_n)
            }
        detalle_extendido[clave] = {
            "total": monto,
            "students_count": cnt_students,
            "months": months_struct
        }

    # detalle_sumario para la UI (clave -> total)
    detalle_sumario = {k: int(v) if float(v).is_integer() else float(v) for k, v in por_paralelo.items()}

    return jsonify({
        "total": int(total) if float(total).is_integer() else float(total),
        "detalle": detalle_sumario,
        "detalle_extendido": detalle_extendido,
        "por_mes": por_mes
    })

@app.route("/report/student")
def report_student():
    ci = request.args.get("ci")
    year = int(request.args.get("year", datetime.now().year))

    # Datos del estudiante
    doc = db.collection("students").document(ci).get()
    if not doc.exists:
        return "Estudiante no encontrado", 404

    est = doc.to_dict()
    curso = est.get("curso", "?")
    paralelo = est.get("paralelo", "?")
    nombre = f"{est.get('first_name','')} {est.get('last_name_p','')} {est.get('last_name_m','')}"

    # Datos de pagos
    pagos = db.collection("payments") \
        .where("student_ci", "==", ci) \
        .where("year", "==", year).stream()

    pagados = {p.to_dict()["month"]: p.to_dict()["amount"] for p in pagos}

    # Preparar PDF
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm

    buffer = io.BytesIO()
    doc_pdf = SimpleDocTemplate(buffer, pagesize=A4)

    styles = getSampleStyleSheet()
    title = ParagraphStyle("title", parent=styles["Heading1"], alignment=1, fontSize=16)
    normal = ParagraphStyle("normal", parent=styles["Normal"], alignment=0)

    flow = []

    # Logo
    logo_path = os.path.join(app.root_path, "static", "img", "logo.png")
    if os.path.exists(logo_path):
        flow.append(Image(logo_path, width=40*mm, height=40*mm))
        flow.append(Spacer(1, 10))

    flow.append(Paragraph(f"Reporte Individual — {year}", title))
    flow.append(Spacer(1, 8))
    flow.append(Paragraph(f"<b>Nombre:</b> {nombre}", normal))
    flow.append(Paragraph(f"<b>CI:</b> {ci}", normal))
    flow.append(Paragraph(f"<b>Curso:</b> {curso} — <b>Paralelo:</b> {paralelo}", normal))
    flow.append(Spacer(1, 12))

    # Tabla por mes
    meses = ["Enero","Febrero","Marzo","Abril","Mayo","Junio","Julio",
             "Agosto","Septiembre","Octubre","Noviembre","Diciembre"]

    tabla = [["Mes", "Estado", "Monto (Bs)"]]

    total = 0
    for i, mes in enumerate(meses, start=1):
        monto = pagados.get(i, 0)
        estado = "PAGADO" if monto > 0 else "NO PAGADO"
        tabla.append([mes, estado, f"{monto} Bs"])
        total += monto

    t = Table(tabla, colWidths=[70*mm, 40*mm, 40*mm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#003366")),
        ("TEXTCOLOR", (0,0), (-1,0), colors.white),
        ("GRID", (0,0), (-1,-1), 0.5, colors.grey),
        ("ALIGN", (0,0), (-1,-1), "CENTER"),
        ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
    ]))

    flow.append(t)
    flow.append(Spacer(1, 15))
    flow.append(Paragraph(f"<b>Total pagado:</b> {total} Bs", normal))

    doc_pdf.build(flow)

    buffer.seek(0)
    return send_file(buffer,
        as_attachment=True,
        download_name=f"reporte_{ci}_{year}.pdf",
        mimetype="application/pdf"
    )


# ============================
# REPORTES PDF (por curso/paralelo) - descarga resumida y por cada clave
# ============================
@app.route("/report/pdf")
def report_pdf():
    year = int(request.args.get("year", datetime.now(ZoneInfo(TZ)).year))

    # Recolectar students map ci -> (curso, paralelo)
    students_docs = db.collection("students").stream()
    students_map = {}
    for sdoc in students_docs:
        s = sdoc.to_dict()
        ci = s.get("ci")
        if ci:
            students_map[ci] = {
                "curso": s.get("curso", "Desconocido"),
                "paralelo": s.get("paralelo", "")
            }

    pagos = db.collection("payments").where("year", "==", year).stream()

    total = 0
    por_paralelo = {}
    pagos_por_clave_mes = {}
    pagos_amount_por_clave_mes = {}
    estudiantes_por_clave = {}

    # llenar estudiantes_por_clave desde students_map
    for ci, info in students_map.items():
        clave = f"{info.get('curso','Desconocido')} {info.get('paralelo','')}".strip()
        estudiantes_por_clave.setdefault(clave, set()).add(ci)

    # Recolectar pagos reales
    for pdoc in pagos:
        d = pdoc.to_dict()
        ci = d.get("student_ci")
        monto = float(d.get("amount", 0))
        mes = int(d.get("month", 0)) if d.get("month") else 0

        curso = d.get("curso")
        paralelo = d.get("paralelo")
        if not curso:
            st = students_map.get(ci)
            if st:
                curso = st.get("curso")
                paralelo = st.get("paralelo")
        if not curso:
            curso = "Desconocido"
            paralelo = ""
        clave = f"{curso} {paralelo}".strip()

        total += monto
        por_paralelo[clave] = por_paralelo.get(clave, 0) + monto

        pagos_por_clave_mes.setdefault(clave, {}).setdefault(mes, set()).add(ci)
        pagos_amount_por_clave_mes.setdefault(clave, {}).setdefault(mes, 0.0)
        pagos_amount_por_clave_mes[clave][mes] += monto

        if ci:
            estudiantes_por_clave.setdefault(clave, set()).add(ci)

    # prepare PDF (reportlab)
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib import colors
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import mm
    except Exception:
        return jsonify({"error": "Instala reportlab: pip install reportlab"}), 500

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=18*mm, leftMargin=18*mm, topMargin=18*mm, bottomMargin=18*mm)

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("title", parent=styles["Heading1"], alignment=1, fontSize=18, spaceAfter=8)
    normal_center = ParagraphStyle("nc", parent=styles["Normal"], alignment=1, fontSize=10)

    flow = []

    # logo
    logo_path = os.path.join(app.root_path, "static", "img", "logo.png")
    if os.path.exists(logo_path):
        try:
            logo = Image(logo_path, width=40*mm, height=40*mm)
            logo.hAlign = 'CENTER'
            flow.append(logo)
            flow.append(Spacer(1, 6))
        except Exception:
            pass

    flow.append(Paragraph(f"Colegio — Reporte Anual {year}", title_style))
    flow.append(Spacer(1, 6))
    flow.append(Paragraph(f"Total recaudado: {int(total) if float(total).is_integer() else round(total,2)} Bs", normal_center))
    flow.append(Spacer(1, 12))

    # Resumen por curso / paralelo
    flow.append(Paragraph("Resumen por Curso / Paralelo", styles["Heading3"]))

    header = ["Curso / Paralelo", "Estudiantes", "Total (Bs)"]
    data_table = [header]

    claves = sorted(set(list(estudiantes_por_clave.keys()) + list(por_paralelo.keys())))
    for clave in claves:
        cnt = len(estudiantes_por_clave.get(clave, set()))
        tot = por_paralelo.get(clave, 0)
        data_table.append([clave if clave else "Desconocido", str(cnt), f"{int(tot) if float(tot).is_integer() else round(tot,2)} Bs"])

    # Añadir fila TOTAL al final
    total_students = sum(len(v) for v in estudiantes_por_clave.values())
    data_table.append(["TOTAL", str(total_students), f"{int(total) if float(total).is_integer() else round(total,2)} Bs"])

    # table widths: adaptar a A4 para que no corten
    table_col_widths = [100*mm, 30*mm, 40*mm]
    t = Table(data_table, colWidths=table_col_widths)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#003366")),
        ("TEXTCOLOR", (0,0), (-1,0), colors.white),
        ("ALIGN", (0,0), (-1,-1), "CENTER"),
        ("GRID", (0,0), (-1,-1), 0.5, colors.grey),
        ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
        ("BOTTOMPADDING", (0,0), (-1,0), 8),
    ]))
    flow.append(t)
    flow.append(Spacer(1, 12))

    # Detalle por cada clave (curso/paralelo)
    months_names = ["Enero","Febrero","Marzo","Abril","Mayo","Junio","Julio","Agosto","Septiembre","Octubre","Noviembre","Diciembre"]
    for clave in claves:
        flow.append(Paragraph(f"Detalle — {clave if clave else 'Desconocido'}", styles["Heading4"]))
        rows = [["Mes", "Pagaron (n)", "No pagaron (n)", "Monto recaudado (Bs)"]]
        students_count = len(estudiantes_por_clave.get(clave, set()))
        pagos_por_mes = pagos_por_clave_mes.get(clave, {})
        pagos_amount_mes = pagos_amount_por_clave_mes.get(clave, {})
        for m in range(1, 13):
            paid_set = pagos_por_mes.get(m, set())
            paid_n = len(paid_set)
            not_paid_n = max(0, students_count - paid_n)
            monto = pagos_amount_mes.get(m, 0.0)
            rows.append([months_names[m-1], str(paid_n), str(not_paid_n), f"{int(monto) if float(monto).is_integer() else round(monto,2)} Bs"])
        table_det = Table(rows, colWidths=[60*mm, 30*mm, 30*mm, 40*mm])
        table_det.setStyle(TableStyle([
            ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#1e90ff")),
            ("TEXTCOLOR", (0,0), (-1,0), colors.white),
            ("ALIGN", (0,0), (-1,-1), "CENTER"),
            ("GRID", (0,0), (-1,-1), 0.4, colors.grey),
            ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
        ]))
        flow.append(table_det)
        flow.append(Spacer(1, 10))

    flow.append(Spacer(1, 8))
    flow.append(Paragraph(f"Generado: {datetime.now(ZoneInfo(TZ)).strftime('%Y-%m-%d %H:%M:%S')}", styles["Normal"]))

    # build PDF
    doc.build(flow)
    buffer.seek(0)
    return send_file(
        buffer,
        as_attachment=True,
        download_name=f"reporte_{year}.pdf",
        mimetype="application/pdf"
    )


# ============================
# RUN
# ============================
if __name__ == "__main__":
    app.run(debug=True)
