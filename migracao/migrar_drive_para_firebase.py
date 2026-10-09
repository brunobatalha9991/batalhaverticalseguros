# -*- coding: utf-8 -*-
"""
Migra o JSON exportado do Google Drive (crm-seguros-data.json) para o Firebase.

Uso:
  python -I migrar_drive_para_firebase.py <caminho do crm-seguros-data.json> [--admin EMAIL] [--ignorar EMAIL[,EMAIL]] [--executar]

Sem --executar o script só mostra o que faria (modo simulação).

O que o script faz:
  1. Lê o JSON do Drive (cards, etapas, listas e usuários).
  2. Para cada usuário do CRM antigo, cria (se não existir) uma conta no
     Firebase Authentication com senha aleatória e um perfil em crm_usuarios.
     Como a senha antiga não é recuperável, cada pessoa define a nova senha
     pelo link "Esqueci minha senha" na tela de login.
  3. Grava todos os cards em crm_cards e as configurações em crm_config/geral.
  4. Só escreve nas coleções crm_*. Nunca toca em usuarios/ ou ceo/.

Autenticação: usa o login do Firebase CLI já feito nesta máquina
(firebase login), pela mesma troca de token que o próprio CLI faz.
"""
import io, json, os, sys, secrets, urllib.request, urllib.parse, urllib.error

PROJECT = 'gc---vertical-seguros'
COL_USUARIOS, COL_CARDS, COL_CONFIG, DOC_CONFIG = 'crm_usuarios', 'crm_cards', 'crm_config', 'geral'
FS_BASE = f'https://firestore.googleapis.com/v1/projects/{PROJECT}/databases/(default)/documents'
DEFAULT_LISTS = {
    'responsaveis': ['Administrador'],
    'seguradoras': ['Porto Seguro', 'SulAmérica', 'Bradesco Seguros', 'Allianz', 'Mapfre', 'HDI', 'Tokio Marine'],
    'ramos': ['Automóvel', 'Vida', 'Residencial', 'Empresarial', 'Saúde', 'Viagem', 'Rural', 'Responsabilidade Civil'],
    'fontes': ['Indicação', 'Orgânico', 'Tráfego', 'Carteira'],
}


# ───────────────────────── token do Firebase CLI ─────────────────────────
def access_token():
    cfg = os.path.join(os.environ.get('USERPROFILE') or os.path.expanduser('~'), '.config', 'configstore', 'firebase-tools.json')
    d = json.load(io.open(cfg, encoding='utf-8'))
    rt = (d.get('tokens') or {}).get('refresh_token')
    if not rt:
        sys.exit('Faça "firebase login" antes de rodar a migração.')
    body = urllib.parse.urlencode({
        'client_id': '563584335869-fgrhgmd47bqnekij5i8b5pr03ho849e6.apps.googleusercontent.com',
        'client_secret': 'j9iVZfS8kkCEFUPaAeJV0sAi',
        'refresh_token': rt, 'grant_type': 'refresh_token'}).encode()
    r = urllib.request.urlopen(urllib.request.Request('https://oauth2.googleapis.com/token', data=body))
    return json.load(r)['access_token']


TOKEN = None


def call(method, url, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={'Authorization': 'Bearer ' + TOKEN, 'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req) as r:
            txt = r.read().decode()
            return json.loads(txt) if txt else {}
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        raise RuntimeError(f'{method} {url} -> HTTP {e.code}: {body[:400]}')


# ───────────────────────── JSON ⇄ Firestore ─────────────────────────
def to_fs(v):
    if v is None:
        return {'nullValue': None}
    if isinstance(v, bool):
        return {'booleanValue': v}
    if isinstance(v, int):
        return {'integerValue': str(v)}
    if isinstance(v, float):
        return {'doubleValue': v}
    if isinstance(v, str):
        return {'stringValue': v}
    if isinstance(v, list):
        return {'arrayValue': {'values': [to_fs(x) for x in v]}}
    if isinstance(v, dict):
        return {'mapValue': {'fields': {k: to_fs(x) for k, x in v.items() if x is not None or True}}}
    return {'stringValue': str(v)}


def doc_fields(obj):
    return {k: to_fs(v) for k, v in obj.items()}


def batch_write(writes):
    """writes: lista de (collection, doc_id, dict). Lotes de 400."""
    for i in range(0, len(writes), 400):
        chunk = writes[i:i + 400]
        payload = {'writes': [{'update': {'name': f'projects/{PROJECT}/databases/(default)/documents/{c}/{d}', 'fields': doc_fields(o)}} for c, d, o in chunk]}
        res = call('POST', f'{FS_BASE}:batchWrite', payload)
        for j, st in enumerate(res.get('status', [])):
            if st.get('code', 0) != 0:
                raise RuntimeError(f'Falha ao gravar {chunk[j][0]}/{chunk[j][1]}: {st}')
        print(f'  gravados {min(i + 400, len(writes))}/{len(writes)}')


def list_collection_ids(col):
    ids, token = [], None
    while True:
        q = f'{FS_BASE}/{col}?pageSize=300&mask.fieldPaths=id' + (f'&pageToken={token}' if token else '')
        res = call('GET', q)
        ids += [d['name'].rsplit('/', 1)[-1] for d in res.get('documents', [])]
        token = res.get('nextPageToken')
        if not token:
            return ids


# ───────────────────────── Firebase Auth (admin via Identity Toolkit) ─────────────────────────
def auth_lookup(email):
    res = call('POST', f'https://identitytoolkit.googleapis.com/v1/projects/{PROJECT}/accounts:lookup', {'email': [email]})
    users = res.get('users') or []
    return users[0] if users else None


def auth_create(email, nome):
    pwd = secrets.token_urlsafe(24)
    res = call('POST', f'https://identitytoolkit.googleapis.com/v1/projects/{PROJECT}/accounts',
               {'email': email, 'password': pwd, 'displayName': nome, 'emailVerified': False})
    return res['localId']


# ───────────────────────── migração ─────────────────────────
def main():
    global TOKEN
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    if not args:
        sys.exit(__doc__)
    executar = '--executar' in sys.argv
    admin_email = None
    if '--admin' in sys.argv:
        admin_email = sys.argv[sys.argv.index('--admin') + 1].strip().lower()
    ignorar = set()
    if '--ignorar' in sys.argv:
        ignorar = {e.strip().lower() for e in sys.argv[sys.argv.index('--ignorar') + 1].split(',') if e.strip()}
    src = args[0]
    data = json.load(io.open(src, encoding='utf-8'))
    if not (data.get('stages') and 'cards' in data):
        sys.exit('JSON inválido: esperado stages e cards.')

    cards = [c for c in data['cards'] if c and c.get('id')]
    users = [u for u in data.get('users') or [] if u and u.get('email')]
    print(f'Arquivo: {src}')
    print(f'  cards: {len(cards)} (ativos: {sum(1 for c in cards if not c.get("deleted"))}, excluídos: {sum(1 for c in cards if c.get("deleted"))})')
    print(f'  etapas: {len(data["stages"])}  responsáveis: {len(data.get("responsaveis") or [])}')
    print(f'  usuários no CRM antigo: {len(users)}')
    for u in users:
        print(f'    - {u.get("nome")} <{u.get("email")}> role={u.get("role")} ativo={u.get("active")}')
    if admin_email:
        print(f'  administrador garantido: {admin_email}')
    if ignorar:
        print(f'  ignorados: {", ".join(sorted(ignorar))}')
    if not executar:
        print('\nModo simulação. Rode de novo com --executar para gravar no Firebase.')
        return

    TOKEN = access_token()

    # 1) usuários
    print('\n[1/3] Usuários')
    perfis = []
    seen = set()
    todos = list(users)
    if admin_email and admin_email not in {u['email'].lower() for u in users}:
        todos.append({'nome': 'Administrador', 'email': admin_email, 'role': 'admin', 'active': True})
    for u in todos:
        email = u['email'].strip().lower()
        if email in seen or email == 'admin@crm.com' or email in ignorar:
            if email == 'admin@crm.com':
                print(f'  - {email}: usuário padrão fictício, ignorado')
            elif email in ignorar:
                print(f'  - {email}: ignorado a pedido (--ignorar)')
            continue
        seen.add(email)
        nome = (u.get('nome') or email.split('@')[0]).strip()
        role = 'admin' if (u.get('role') == 'admin' or email == admin_email) else 'user'
        active = bool(u.get('active')) or email == admin_email
        acc = auth_lookup(email)
        if acc:
            uid = acc['localId']
            print(f'  - {email}: login já existia no Firebase (uid {uid[:8]}…)')
        else:
            uid = auth_create(email, nome)
            print(f'  - {email}: login criado (uid {uid[:8]}…), senha definida pelo "Esqueci minha senha"')
        now = u.get('updatedAt') or u.get('createdAt') or '2026-01-01T00:00:00.000Z'
        perfis.append((COL_USUARIOS, uid, {'id': uid, 'nome': nome, 'email': email, 'role': role, 'active': active,
                                            'createdAt': u.get('createdAt') or now, 'updatedAt': now, 'migradoDoDrive': True}))
    if perfis:
        batch_write(perfis)

    # 2) cards
    print('\n[2/3] Cards')
    existentes = set(list_collection_ids(COL_CARDS))
    if existentes:
        print(f'  atenção: já existem {len(existentes)} cards no Firebase; os de mesmo id serão sobrescritos pelo arquivo')
    batch_write([(COL_CARDS, c['id'], c) for c in cards])

    # 3) configuração
    print('\n[3/3] Configuração (etapas e listas)')
    cfg = {'stages': data['stages']}
    for k in DEFAULT_LISTS:
        cfg[k] = data.get(k) or DEFAULT_LISTS[k]
    cfg['updatedAt'] = '2026-01-01T00:00:00.000Z'
    cfg['updatedBy'] = 'migracao-drive'
    batch_write([(COL_CONFIG, DOC_CONFIG, cfg)])

    print('\nMigração concluída.')
    print('Cada usuário entra em https://crm-vertical-seguros.web.app e usa "Esqueci minha senha" para definir a senha.')


if __name__ == '__main__':
    main()
