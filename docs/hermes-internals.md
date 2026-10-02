# Hermes por dentro

Caderno de estudo: como o Hermes funciona debaixo dos panos, na medida do que o plugin precisa.

> **Versão de referência do `hermes-agent`:** commit [`e05b16348b1d06a3311237423b0a4fc30d9c5aa1`](https://github.com/NousResearch/hermes-agent/commit/e05b16348b1d06a3311237423b0a4fc30d9c5aa1) (1 de outubro de 2026).  
> Todos os links de arquivos e linhas abaixo apontam para esta árvore fixa no GitHub.

---

## 1. Perfil

Instância lógica isolada com configuração, credenciais, banco de dados e diretório próprios.

* **Localização no disco:**
  * Perfil padrão: `~/.hermes/`
  * Perfis secundários: `~/.hermes/profiles/<nome>/`
* **Resolução de Caminhos e Identidade:**
  * [`hermes_constants.py`](https://github.com/NousResearch/hermes-agent/blob/e05b16348b1d06a3311237423b0a4fc30d9c5aa1/hermes_constants.py): `get_hermes_home()` resolve o diretório do perfil ativo; `named_profile_has_identity()` verifica presença de `config.yaml`, `SOUL.md`, `.env` ou `state.db`.
  * [`hermes_cli/profiles.py:profiles_to_serve()`](https://github.com/NousResearch/hermes-agent/blob/e05b16348b1d06a3311237423b0a4fc30d9c5aa1/hermes_cli/profiles.py): Enumera os perfis ativos servidos pelo gateway.
* **Isolamento de Escopo:**
  * [`gateway/run.py:_profile_runtime_scope`](https://github.com/NousResearch/hermes-agent/blob/e05b16348b1d06a3311237423b0a4fc30d9c5aa1/gateway/run.py): Vincula o escopo do perfil ativo durante turnos e callbacks.
  * [`agent/secret_scope.py`](https://github.com/NousResearch/hermes-agent/blob/e05b16348b1d06a3311237423b0a4fc30d9c5aa1/agent/secret_scope.py): Sob multiplexação, leituras fora de escopo levantam `UnscopedSecretError`, impedindo vazamento de segredos entre perfis.

---

## 2. Gateway

O processo supervisor de mensageria e ciclo de vida do agente.

* **Arquivos centrais:**
  * [`gateway/run.py`](https://github.com/NousResearch/hermes-agent/blob/e05b16348b1d06a3311237423b0a4fc30d9c5aa1/gateway/run.py) (fachada do runner).
  * [`gateway/run_turn.py`](https://github.com/NousResearch/hermes-agent/blob/e05b16348b1d06a3311237423b0a4fc30d9c5aa1/gateway/run_turn.py) (preparação e execução de turnos).
* **Ciclo de Inbound e Preparação de Turno:**
  * [`gateway/run_turn.py:2045`](https://github.com/NousResearch/hermes-agent/blob/e05b16348b1d06a3311237423b0a4fc30d9c5aa1/gateway/run_turn.py#L2045) (`_hmwa_prepare_turn`): Resolve a sessão, monta o contexto da sessão (`build_session_context:2052`), renderiza o prompt fixado da sessão (`_pinned_session_context_prompt:2064`) e prepara notas de sidecar (`turn_sidecar_notes`).
* **Emissão de Eventos de Ciclo de Vida:**
  * [`gateway/run_turn.py:2175`](https://github.com/NousResearch/hermes-agent/blob/e05b16348b1d06a3311237423b0a4fc30d9c5aa1/gateway/run_turn.py#L2175): Dispara o evento de hook `agent:start` contendo `platform`, `user_id`, `chat_id`, `thread_id`, `chat_type`, `session_id` e `message` (truncada em 500 caracteres).
* **Hooks de Gateway (`gateway/hooks.py`):**
  * Localizados em `<profile_home>/hooks/<name>/` (`HOOK.yaml` + `handler.py`). Diferente dos hooks de plugin (`hermes_cli/plugins.py`), estes são observadores estritos de infraestrutura e não alteram o texto de entrada do modelo.

---

## 3. `state.db`

Banco SQLite local de cada perfil (`<profile_home>/state.db`), operando em modo WAL.

* **Arquivo de definição de schema:** [`hermes_state_common.py:381`](https://github.com/NousResearch/hermes-agent/blob/e05b16348b1d06a3311237423b0a4fc30d9c5aa1/hermes_state_common.py#L381).

### Colunas de interesse para o plugin

* **Tabela `sessions`:**
  * `id TEXT PRIMARY KEY`: Identificador único da sessão.
  * `source TEXT NOT NULL`: Origem da sessão (`telegram`, `desktop`, `cli`, `cron`, `kanban`, etc.).
  * `user_id TEXT`: Identificador do usuário na plataforma de mensageria.
  * `display_name TEXT`: Nome de exibição do usuário.
  * `started_at REAL NOT NULL`: Timestamp de início da sessão.
  * `message_count INTEGER DEFAULT 0`: Quantidade total de mensagens trocadas.
  * `estimated_cost_usd REAL` / `actual_cost_usd REAL`: Métricas de custo de inferência da sessão.
  * `profile_name TEXT`: Nome do perfil executado.
* **Tabela `messages`:**
  * `id INTEGER PRIMARY KEY AUTOINCREMENT`: Identificador sequencial.
  * `session_id TEXT NOT NULL REFERENCES sessions(id)`: Chave estrangeira para a sessão.
  * `role TEXT NOT NULL`: Papel da mensagem (`user`, `assistant`, `tool`).
  * `content TEXT`: Conteúdo textual da mensagem.
  * `timestamp REAL NOT NULL`: Timestamp da mensagem.

### Heurística de Adoção Humana (Métrica do Projeto)

O Hermes não possui um campo nativo booleano indicando se uma mensagem veio de um humano. Para estimar a adoção humana real, o projeto utiliza a seguinte heurística via SQL (excluindo origens de automação):

```sql
SELECT COUNT(m.id) AS human_interactive_messages
FROM messages m
JOIN sessions s ON m.session_id = s.id
WHERE m.role = 'user'
  AND m.timestamp >= :cutoff_timestamp
  AND s.source NOT IN (
    'cli', 'cron', 'kanban', 'acp', 'api_server', 'subagent', 'tool', 'recovered'
  );
```

#### Ressalvas conhecidas da heurística:
1. **Mensagens em Grupo:** Em conversas de grupo (`chat_type='group'`), o remetente individual de cada mensagem ainda não é verificado de forma atômica por mensagem no schema atual.
2. **Inflação por Administradores:** Operadores e desenvolvedores testando o sistema utilizam canais interativos (`telegram`, `desktop`), inflando as contagens da equipe. Para medir a adoção genuína do departamento, a consulta deve filtrar os `user_id` de administradores conhecidos.

* **Conexão concorrente segura:** Consultas analíticas devem sempre abrir o arquivo em modo somente-leitura URI para não disputar travas com o gateway em produção:  
  `sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=15.0)`

---

## 4. Sistema de Plugins do Agente (`hermes_cli/plugins.py`)

O ponto de extensão principal onde o plugin atua dentro do ciclo de execução do agente.

* **Arquivo central:** [`hermes_cli/plugins.py`](https://github.com/NousResearch/hermes-agent/blob/e05b16348b1d06a3311237423b0a4fc30d9c5aa1/hermes_cli/plugins.py)
* **Ponto de entrada:** Diretório `~/.hermes/plugins/<id>/` contendo `plugin.yaml` e um `__init__.py` exportando a função `register(ctx: PluginContext)`.
* **Hooks válidos (`VALID_HOOKS`, linhas 109-175):** Conjunto estrito de eventos interceptáveis pelo runtime (ex: `pre_llm_call`, `post_llm_call`, `pre_tool_call`, `post_tool_call`, `on_session_start`, `on_session_end`, `kanban_task_claimed`, `kanban_task_completed`).
* **Hooks que alteram o fluxo pelo retorno:** `pre_llm_call` (injeta contexto), `pre_gateway_dispatch` (pula ou reescreve a mensagem antes do agente), `pre_tool_call` (bloqueia a chamada), `transform_tool_result`, `transform_terminal_output`, `transform_llm_output` e `pre_verify`. Os demais só observam.

### Injeção de Contexto da Carta (`pre_llm_call`)
* **Registro:** `ctx.register_hook("pre_llm_call", callback)` ([`hermes_cli/plugins.py:976`](https://github.com/NousResearch/hermes-agent/blob/e05b16348b1d06a3311237423b0a4fc30d9c5aa1/hermes_cli/plugins.py#L976)).
* **Comportamento ([docs/features/hooks.md](https://github.com/NousResearch/hermes-agent/blob/e05b16348b1d06a3311237423b0a4fc30d9c5aa1/website/docs/user-guide/features/hooks.md#pre_llm_call)):** O callback recebe `(session_id, user_message, conversation_history, ...)` e pode retornar uma string ou `{"context": "..."}`.
* **Mecânica de injeção:** O texto retornado é concatenado e anexado à **mensagem de usuário do turno atual** (`current turn's user message`), preservando o prompt de sistema byte-estável para cache de prefixo de LLM. É por este hook que a carta do departamento (`fleet.yaml`: responsabilidades, limites e regras de escalação) entra no contexto do agente a cada turno.
* **Resolução Dinâmica e Hot-Reload:** O plugin resolve `~/.hermes/fleet.yaml` prioritariamente sobre o arquivo local e inspeciona o `st_mtime` a cada turno. Alterações na carta corporativa surtem efeito imediato sem necessidade de restart do Gateway.

### Observador de Resolução de Tarefa (`kanban_task_completed`)
* **Registro:** `ctx.register_hook("kanban_task_completed", callback)` ([`hermes_cli/plugins.py:162`](https://github.com/NousResearch/hermes-agent/blob/e05b16348b1d06a3311237423b0a4fc30d9c5aa1/hermes_cli/plugins.py#L162)).
* **Comportamento ([docs/features/hooks.md](https://github.com/NousResearch/hermes-agent/blob/e05b16348b1d06a3311237423b0a4fc30d9c5aa1/website/docs/user-guide/features/hooks.md#kanban_task_completed)):** Disparado pelo processo worker no momento em que uma tarefa conclui (`status = 'done'`). O hook recebe `(task_id, profile_name, board, assignee, run_id, summary)`.
* **Fechamento de Ciclo:** Permite inspecionar o `kanban.db` em modo somente-leitura, identificar se a tarefa é um handoff interdepartamental (`**From Department:**` no body), persistir o log de auditoria em `~/.hermes/workforce/resolutions.jsonl` e disparar notificações de retorno para o canal/webhook do departamento de origem.

### Registro de Ferramentas (`register_tool`)
* **Registro:** `ctx.register_tool(name, toolset, schema, handler, ...)` ([`hermes_cli/plugins.py:457`](https://github.com/NousResearch/hermes-agent/blob/e05b16348b1d06a3311237423b0a4fc30d9c5aa1/hermes_cli/plugins.py#L457)).
* Permite registrar ferramentas de modelo dinâmicas diretamente no registro global sem alterar o core do Hermes. Ponto de entrada para ferramentas como a delegação interdepartamental (abrir tarefa para outro departamento validando contra `escalates_to`).

### Injeção Ativa de Mensagens (`inject_message`)
* **Chamada:** `ctx.inject_message(content, role="user", session_key=...)` ([`hermes_cli/plugins.py:604`](https://github.com/NousResearch/hermes-agent/blob/e05b16348b1d06a3311237423b0a4fc30d9c5aa1/hermes_cli/plugins.py#L604)).
* Envia uma mensagem para a fila de execução de uma sessão ativa do CLI, Desktop ou Gateway.
* **Requisito de segurança:** Para injeção no Gateway, requer que o operador habilite explicitamente no `config.yaml`:
  ```yaml
  plugins:
    entries:
      <plugin_id>:
        allow_gateway_injection: true
  ```

---

## 5. `kanban.db`

Quadro de tarefas durável e transacional compartilhado entre todos os perfis (`~/.hermes/kanban.db`).

* **Arquivo de definição de schema:** [`hermes_cli/kanban_db.py:875`](https://github.com/NousResearch/hermes-agent/blob/e05b16348b1d06a3311237423b0a4fc30d9c5aa1/hermes_cli/kanban_db.py#L875).

### Colunas de interesse para o plugin

* **Tabela `tasks`:**
  * `id TEXT PRIMARY KEY`: Identificador único da tarefa.
  * `title TEXT NOT NULL` / `body TEXT`: Descrição e especificações do trabalho.
  * `assignee TEXT`: Perfil responsável pela execução da tarefa.
  * `status TEXT NOT NULL`: Estado atual (`triage`, `todo`, `scheduled`, `ready`, `running`, `review`, `blocked`, `done`, `archived`).
  * `created_by TEXT`: Origem ou perfil solicitante da tarefa.
  * `created_at INTEGER NOT NULL` / `started_at INTEGER` / `completed_at INTEGER`: Marcos temporais.
  * `result TEXT`: Resumo ou resultado produzido após conclusão.
  * `session_id TEXT`: ID da sessão do agente que gerou a tarefa.
* **Tabela `task_links` (Linhagem e Dependência):**
  * `parent_id TEXT NOT NULL`, `child_id TEXT NOT NULL` (Chave Primária Composta).
  * Criação do vínculo: [`hermes_cli/kanban_db.py:1446`](https://github.com/NousResearch/hermes-agent/blob/e05b16348b1d06a3311237423b0a4fc30d9c5aa1/hermes_cli/kanban_db.py#L1446) (`INSERT OR IGNORE INTO task_links`).
  * **Trava de Dependência:** [`hermes_cli/kanban_db.py:2198`](https://github.com/NousResearch/hermes-agent/blob/e05b16348b1d06a3311237423b0a4fc30d9c5aa1/hermes_cli/kanban_db.py#L2198) (`_parents_satisfied`). Uma tarefa-filha é retida em `todo`/`blocked` e não transiciona para `ready` enquanto qualquer tarefa-pai listada em `task_links` estiver fora de `done` ou `archived`.

### Como um perfil cria tarefas no Kanban hoje
1. **Via Model Tool (`kanban_create`):** Ferramenta nativa do toolset `kanban` (handler em [`tools/kanban_tools.py:1053`](https://github.com/NousResearch/hermes-agent/blob/e05b16348b1d06a3311237423b0a4fc30d9c5aa1/tools/kanban_tools.py#L1053) e schema em [`tools/kanban_tools_schemas.py:389`](https://github.com/NousResearch/hermes-agent/blob/e05b16348b1d06a3311237423b0a4fc30d9c5aa1/tools/kanban_tools_schemas.py#L389)). Requer o toolset `kanban` habilitado no perfil.
2. **Via CLI no Terminal:** Execução via ferramenta `terminal` do comando `hermes kanban create --title "..." --body "..." --assignee <perfil> [--board <board>]`.

---

## 6. Plugin Backend & Desktop

Arquitetura híbrida de extensão para backend Python e interface Desktop.

* **Backend FastAPI (`plugin_api.py`):**
  * Estrutura de disco: `~/.hermes/plugins/<id>/dashboard/manifest.json` com `"api": "plugin_api.py"`.
  * Montagem de rotas: [`hermes_cli/web_server_dashboard.py:805`](https://github.com/NousResearch/hermes-agent/blob/e05b16348b1d06a3311237423b0a4fc30d9c5aa1/hermes_cli/web_server_dashboard.py#L805) (`_mount_plugin_api_routes`).
  * Contrato: O arquivo deve exportar `router = APIRouter()`, montado automaticamente pelo servidor web em `/api/plugins/<id>/`.
  * Habilitação: O plugin precisa estar listado em `plugins.enabled` no `config.yaml`.
* **Frontend Desktop ESM (`plugin.js`):**
  * Estrutura de disco: `~/.hermes/desktop-plugins/<id>/plugin.js`.
  * Contrato: `export default { id, name, register(ctx) }`.
  * Superfície de importação: `@hermes/plugin-sdk`, `react`, `react/jsx-runtime` (executado diretamente como módulo ESM pelo aplicativo Desktop sem compilação).
  * Comunicação: `ctx.rest('/...')` comunica com o backend FastAPI do plugin; `host.request(method, params)` acessa os métodos JSON-RPC do Gateway.
