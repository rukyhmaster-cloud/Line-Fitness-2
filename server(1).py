#!/usr/bin/env python3
"""Line Fitness v2: contas + treinos da semana (só biblioteca padrão do Python).
Uso:  python3 server.py            -> http://localhost:8000
IA:   export ANTHROPIC_API_KEY=sua-chave   (sem chave, usa o gerador padrão da casa)
Produção: LF_SECURE=1 atrás de HTTPS (cookie Secure)."""
import hashlib, hmac, json, os, re, secrets, sqlite3, time, urllib.request
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path

ROOT = Path(__file__).parent
DB = os.environ.get("LF_DB", str(ROOT / "linefitness.db"))
KEY = os.environ.get("ANTHROPIC_API_KEY", "")
MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5-5")
SECURE = os.environ.get("LF_SECURE") == "1"
DIAS = ["segunda", "terca", "quarta", "quinta", "sexta"]
OBJ, NIV, SEXO = ("hipertrofia", "emagrecimento", "condicionamento"), ("iniciante", "intermediario", "avancado"), ("masculino", "feminino", "nao_informar")

def conn():
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row; return c

with conn() as c:
    c.executescript("""CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, email TEXT UNIQUE, nome TEXT, pw TEXT,
      idade INT, peso REAL, altura INT, sexo TEXT, objetivo TEXT, nivel TEXT);
    CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, user_id INT, exp REAL);
    CREATE TABLE IF NOT EXISTS plans(user_id INTEGER PRIMARY KEY, data TEXT, fonte TEXT, criado REAL);
    CREATE TABLE IF NOT EXISTS execucoes(user_id INT, dia TEXT, nome TEXT, status TEXT, ts REAL, PRIMARY KEY(user_id,dia,nome));""")
    try: c.execute("ALTER TABLE users ADD COLUMN telefone TEXT")  # bancos criados na v2 inicial
    except sqlite3.OperationalError: pass

def hash_pw(p):
    s = os.urandom(16); return "scrypt$%s$%s" % (s.hex(), hashlib.scrypt(p.encode(), salt=s, n=2**14, r=8, p=1).hex())
def check_pw(p, st):
    try:
        _, s, h = st.split("$"); return hmac.compare_digest(hashlib.scrypt(p.encode(), salt=bytes.fromhex(s), n=2**14, r=8, p=1).hex(), h)
    except Exception: return False

# ---------- Treinos ----------
BANCO = {
 "segunda": ("Peito e tríceps", [("Supino reto com barra", "Deite no banco com os pés firmes no chão. Desça a barra até o meio do peito e empurre sem travar os cotovelos."),
   ("Supino inclinado com halteres", "Banco a 30 graus. Suba os halteres juntos acima do peito e desça devagar, sentindo o alongamento."),
   ("Crucifixo na máquina (peck deck)", "Costas apoiadas no encosto. Una os braços à frente do peito com os cotovelos levemente flexionados."),
   ("Tríceps na polia com corda", "Cotovelos colados ao corpo. Estenda os braços para baixo e abra a corda no fim do movimento."),
   ("Tríceps testa com barra W", "Deitado, desça a barra em direção à testa só flexionando os cotovelos e estenda de volta.")]),
 "terca": ("Costas e bíceps", [("Puxada frontal na polia", "Segure a barra um pouco além dos ombros. Puxe até a altura do queixo, levando os cotovelos para baixo."),
   ("Remada baixa sentada", "Tronco ereto e peito aberto. Puxe o triângulo até o abdômen e volte controlando."),
   ("Remada curvada com barra", "Joelhos semiflexionados e coluna neutra. Leve a barra ao abdômen sem balançar o tronco."),
   ("Rosca direta com barra", "Cotovelos fixos ao lado do corpo. Suba a barra sem impulso e desça devagar."),
   ("Rosca martelo com halteres", "Pegada neutra (palmas voltadas uma para a outra). Alterne os braços sem mexer os ombros.")]),
 "quarta": ("Pernas", [("Agachamento livre", "Pés na largura dos ombros. Desça como se fosse sentar, joelhos alinhados com os pés, e suba empurrando o chão."),
   ("Leg press 45°", "Pés na plataforma na largura dos ombros. Desça até 90° nos joelhos e empurre sem travar as pernas."),
   ("Cadeira extensora", "Ajuste o encosto para o joelho ficar na linha do eixo. Estenda as pernas e desça devagar."),
   ("Mesa flexora", "Deitado de bruços, flexione os joelhos levando os calcanhares em direção ao glúteo."),
   ("Panturrilha em pé", "Suba na ponta dos pés o máximo possível, pause um segundo e desça alongando.")]),
 "quinta": ("Ombros", [("Desenvolvimento com halteres", "Sentado, suba os halteres acima da cabeça sem arquear as costas e desça até a altura das orelhas."),
   ("Elevação lateral", "Cotovelos levemente flexionados. Eleve os braços até a altura dos ombros e desça devagar."),
   ("Elevação frontal com anilha", "Segure a anilha com as duas mãos e eleve até a altura dos olhos, sem balançar o corpo."),
   ("Crucifixo invertido na máquina", "Peito apoiado no encosto. Abra os braços para trás, juntando as escápulas."),
   ("Encolhimento com halteres", "Em pé, suba os ombros em direção às orelhas, segure um instante e solte.")]),
 "sexta": ("Full body e core", [("Agachamento com salto", "Agache e salte com força, aterrissando de forma suave com os joelhos flexionados."),
   ("Flexão de braço", "Corpo alinhado da cabeça aos calcanhares. Desça o peito perto do chão e empurre de volta."),
   ("Remada unilateral com halter", "Apoie uma mão no banco. Puxe o halter em direção ao quadril e desça controlando."),
   ("Abdominal na máquina ou solo", "Lombar apoiada. Contraia o abdômen para elevar o tronco, sem puxar o pescoço."),
   ("Prancha abdominal", "Apoie os antebraços e mantenha o corpo reto, abdômen contraído e quadril alinhado.", "40 s")]),
}
PARAM = {"hipertrofia": (4, "8-12", 75), "emagrecimento": (3, "12-15", 40), "condicionamento": (3, "15-20", 30)}

def plano_local(u):
    s, r, d = PARAM[u["objetivo"]]
    if u["nivel"] == "iniciante": s = min(s, 3)
    if u["nivel"] == "avancado" and u["objetivo"] == "hipertrofia": s += 1
    if u["idade"] >= 55: d += 15
    n = 4 if u["nivel"] == "iniciante" else 5
    dias = {}
    for dia, (foco, exs) in BANCO.items():
        dias[dia] = {"foco": foco, "exercicios": [{"nome": e[0], "series": s, "repeticoes": e[2] if len(e) > 2 else r, "descanso": d, "instrucoes": e[1]} for e in exs[:n]]}
    return dias

def limpa(dias):
    """Valida o JSON vindo da IA; devolve None se algo estiver fora do formato."""
    out = {}
    for dia in DIAS:
        x = dias[dia]; exs = []
        for e in x["exercicios"][:10]:
            exs.append({"nome": str(e["nome"])[:80], "series": max(1, min(8, int(e["series"]))), "repeticoes": str(e["repeticoes"])[:20],
                        "descanso": max(0, min(300, int(e["descanso"]))), "instrucoes": str(e["instrucoes"])[:400]})
        if len(exs) < 3: return None
        out[dia] = {"foco": str(x["foco"])[:60], "exercicios": exs}
    return out

def plano_ia(u):
    pedido = ("Monte um treino de musculação de segunda a sexta em português do Brasil para: objetivo %(objetivo)s, nível %(nivel)s, %(idade)s anos, "
              "%(peso)s kg, %(altura)s cm, sexo %(sexo)s. Segunda: peito e tríceps; terça: costas e bíceps; quarta: pernas; quinta: ombros; sexta: full body ou core. "
              "Responda SOMENTE com JSON, sem markdown, neste formato: {\"segunda\":{\"foco\":\"...\",\"exercicios\":[{\"nome\":\"...\",\"series\":3,\"repeticoes\":\"10-12\",\"descanso\":60,\"instrucoes\":\"texto curto de execução\"}]},\"terca\":{...},\"quarta\":{...},\"quinta\":{...},\"sexta\":{...}}. "
              "Use de 4 a 6 exercícios por dia, descanso em segundos e instruções curtas e seguras." % u)
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", method="POST",
        data=json.dumps({"model": MODEL, "max_tokens": 4000, "messages": [{"role": "user", "content": pedido}]}).encode(),
        headers={"content-type": "application/json", "x-api-key": KEY, "anthropic-version": "2023-06-01"})
    txt = json.load(urllib.request.urlopen(req, timeout=60))["content"][0]["text"]
    return limpa(json.loads(txt[txt.index("{"): txt.rindex("}") + 1]))

def gerar(u):
    if KEY:
        try:
            p = plano_ia(u)
            if p: return p, "ia"
        except Exception as e: print("IA indisponível, usando gerador padrão:", type(e).__name__)
    return plano_local(u), "modelo"

def salvar_plano(u):
    p, fonte = gerar(u)
    with conn() as c:
        c.execute("REPLACE INTO plans VALUES(?,?,?,?)", (u["id"], json.dumps(p), fonte, time.time()))
        c.execute("DELETE FROM execucoes WHERE user_id=?", (u["id"],))  # treino novo: zera o que foi marcado
    return {"dias": p, "fonte": fonte, "execucoes": {}}

def execs(uid):
    with conn() as c: rows = c.execute("SELECT dia,nome,status FROM execucoes WHERE user_id=?", (uid,)).fetchall()
    out = {}
    for r in rows: out.setdefault(r["dia"], {})[r["nome"]] = r["status"]
    return out

def desempenho(uid):
    """Nota 0-10 do último treino marcado: exercícios concluídos / total do dia."""
    with conn() as c:
        ult = c.execute("SELECT dia FROM execucoes WHERE user_id=? ORDER BY ts DESC LIMIT 1", (uid,)).fetchone()
        pl = c.execute("SELECT data FROM plans WHERE user_id=?", (uid,)).fetchone()
        if not (ult and pl): return {"nota": None}
        dia = ult["dia"]; x = json.loads(pl["data"])[dia]
        st = {r["nome"]: r["status"] for r in c.execute("SELECT nome,status FROM execucoes WHERE user_id=? AND dia=?", (uid, dia))}
    exs = x["exercicios"]; feitos = sum(1 for e in exs if st.get(e["nome"]) == "concluido")
    return {"nota": round(feitos / len(exs) * 10, 1), "dia": dia, "foco": x["foco"], "feitos": feitos, "total": len(exs),
            "pulados": [e["nome"] for e in exs if st.get(e["nome"]) == "pulado"]}

# ---------- HTTP ----------
EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]{2,}$")
falhas, ultimo = {}, {}

def valida_cadastro(d):
    try:
        u = {"telefone": re.sub(r"\D", "", str(d.get("telefone", ""))), "nome": str(d["nome"]).strip(), "email": str(d["email"]).strip().lower(), "senha": str(d["senha"]), "idade": int(d["idade"]),
             "peso": float(d["peso"]), "altura": int(d["altura"]), "sexo": d["sexo"], "objetivo": d["objetivo"], "nivel": d["nivel"]}
    except Exception: return None, "Preencha todos os campos corretamente."
    if len(u["nome"]) < 2 or len(u["nome"]) > 80: return None, "Informe seu nome."
    if not EMAIL.match(u["email"]): return None, "Informe um e-mail válido."
    if len(u["telefone"]) not in (10, 11): return None, "Informe o telefone com DDD, por exemplo (11) 90000-0000."
    if len(u["senha"]) < 6: return None, "A senha precisa ter pelo menos 6 caracteres."
    if not 12 <= u["idade"] <= 100: return None, "Informe uma idade entre 12 e 100 anos."
    if not 30 <= u["peso"] <= 300: return None, "Informe um peso entre 30 e 300 kg."
    if not 100 <= u["altura"] <= 250: return None, "Informe uma altura entre 100 e 250 cm."
    if u["sexo"] not in SEXO or u["objetivo"] not in OBJ or u["nivel"] not in NIV: return None, "Selecione sexo, objetivo e nível."
    return u, None

class H(BaseHTTPRequestHandler):
    server_version = "LineFitness"
    def log_message(self, *a): pass
    def send(self, code, obj=None, cookie=None, raw=None, ctype="application/json; charset=utf-8"):
        body = raw if raw is not None else json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code); self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff"); self.send_header("X-Frame-Options", "DENY"); self.send_header("Cache-Control", "no-store")
        if cookie: self.send_header("Set-Cookie", cookie)
        self.end_headers(); self.wfile.write(body)
    def user(self):
        m = re.search(r"lf_session=([\w-]+)", self.headers.get("Cookie", ""))
        if not m: return None
        with conn() as c:
            r = c.execute("SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=? AND s.exp>?", (m.group(1), time.time())).fetchone()
        return dict(r) if r else None
    def corpo(self):
        if "application/json" not in self.headers.get("Content-Type", ""): return None
        try: return json.loads(self.rfile.read(min(int(self.headers.get("Content-Length", 0)), 20000)) or b"{}")
        except Exception: return None
    def sessao(self, uid):
        t = secrets.token_urlsafe(32)
        with conn() as c: c.execute("INSERT INTO sessions VALUES(?,?,?)", (t, uid, time.time() + 7 * 86400))
        return "lf_session=%s; HttpOnly; SameSite=Lax; Path=/; Max-Age=%d%s" % (t, 7 * 86400, "; Secure" if SECURE else "")
    def publico(self, u): return {k: u[k] for k in ("nome", "email", "telefone", "idade", "peso", "altura", "sexo", "objetivo", "nivel")}

    def do_GET(self):
        p = self.path.split("?")[0]
        u = self.user()
        if p in ("/", "/index.html", "/conta.html"):
            # Entrada do site: quem não está logado vê a tela de boas-vindas (criar conta / entrar);
            # depois de entrar, "/" mostra o site definitivo.
            f = ROOT / ("conta.html" if p == "/conta.html" or not u else "index.html")
            return self.send(200, raw=f.read_bytes(), ctype="text/html; charset=utf-8")
        if p == "/api/me": return self.send(200, self.publico(u)) if u else self.send(401, {"erro": "Faça login para continuar."})
        if p == "/api/treinos":
            if not u: return self.send(401, {"erro": "Faça login para ver seus treinos."})
            with conn() as c: r = c.execute("SELECT data,fonte FROM plans WHERE user_id=?", (u["id"],)).fetchone()
            return self.send(200, {"dias": json.loads(r["data"]), "fonte": r["fonte"], "execucoes": execs(u["id"])} if r else salvar_plano(u))
        if p == "/api/desempenho":
            return self.send(200, desempenho(u["id"])) if u else self.send(401, {"erro": "Faça login para ver seu desempenho."})
        self.send(404, {"erro": "Página não encontrada."})

    def do_POST(self):
        p, d = self.path.split("?")[0], self.corpo()
        if d is None: return self.send(400, {"erro": "Requisição inválida."})
        if p == "/api/register":
            u, e = valida_cadastro(d)
            if e: return self.send(422, {"erro": e})
            try:
                with conn() as c: cur = c.execute("INSERT INTO users(email,nome,pw,idade,peso,altura,sexo,objetivo,nivel,telefone) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (u["email"], u["nome"], hash_pw(u["senha"]), u["idade"], u["peso"], u["altura"], u["sexo"], u["objetivo"], u["nivel"], u["telefone"]))
            except sqlite3.IntegrityError: return self.send(409, {"erro": "Este e-mail já tem cadastro. Tente entrar."})
            return self.send(201, {"ok": True}, cookie=self.sessao(cur.lastrowid))
        if p == "/api/login":
            email = str(d.get("email", "")).strip().lower(); n, t = falhas.get(email, (0, 0))
            if n >= 5 and time.time() - t < 60: return self.send(429, {"erro": "Muitas tentativas. Aguarde 1 minuto e tente de novo."})
            with conn() as c: r = c.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
            if not (r and check_pw(str(d.get("senha", "")), r["pw"])):
                falhas[email] = (n + 1 if time.time() - t < 60 else 1, time.time())
                return self.send(401, {"erro": "E-mail ou senha incorretos."})
            falhas.pop(email, None); return self.send(200, {"ok": True}, cookie=self.sessao(r["id"]))
        if p == "/api/logout":
            m = re.search(r"lf_session=([\w-]+)", self.headers.get("Cookie", ""))
            if m:
                with conn() as c: c.execute("DELETE FROM sessions WHERE token=?", (m.group(1),))
            return self.send(200, {"ok": True}, cookie="lf_session=; Path=/; Max-Age=0")
        if p == "/api/treinos/gerar":
            u = self.user()
            if not u: return self.send(401, {"erro": "Faça login para gerar treinos."})
            if time.time() - ultimo.get(u["id"], 0) < 8: return self.send(429, {"erro": "Aguarde alguns segundos antes de gerar de novo."})
            ultimo[u["id"]] = time.time(); return self.send(200, salvar_plano(u))
        if p == "/api/execucao":
            u = self.user()
            if not u: return self.send(401, {"erro": "Faça login para marcar seus exercícios."})
            dia, nome, st = d.get("dia"), str(d.get("nome", "")), d.get("status")
            if dia not in DIAS or st not in ("concluido", "pulado", "limpar"): return self.send(422, {"erro": "Marcação inválida."})
            with conn() as c:
                pl = c.execute("SELECT data FROM plans WHERE user_id=?", (u["id"],)).fetchone()
                if not pl or nome not in [e["nome"] for e in json.loads(pl["data"])[dia]["exercicios"]]:
                    return self.send(422, {"erro": "Esse exercício não está no seu treino."})
                if st == "limpar": c.execute("DELETE FROM execucoes WHERE user_id=? AND dia=? AND nome=?", (u["id"], dia, nome))
                else: c.execute("REPLACE INTO execucoes VALUES(?,?,?,?,?)", (u["id"], dia, nome, st, time.time()))
            return self.send(200, {"ok": True})
        self.send(404, {"erro": "Rota não encontrada."})

if __name__ == "__main__":
    porta = int(os.environ.get("PORT", 8000))
    print("Line Fitness em http://localhost:%d  |  IA: %s" % (porta, "ligada (%s)" % MODEL if KEY else "desligada (gerador padrão)"))
    ThreadingHTTPServer(("0.0.0.0", porta), H).serve_forever()
