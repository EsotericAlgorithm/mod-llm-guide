"""GM Admin Mode tools: raw SQL and SOAP console access.

These tools are never part of GAME_TOOLS and are never offered to the
normal player-facing `.ag` flow. They are only attached to a
GameToolExecutor for the duration of a request that the C++ side has
already gated at SEC_ADMINISTRATOR (the `.agm` command, see
LLMGuideScript.cpp) and flagged `is_admin = 1` in `llm_guide_queue`.
There is no allowlist here by design (Matt's call): the model can run
any SQL statement against the configured AzerothCore databases or any
SOAP console command, exactly as a human admin could from the console.
Every call is logged (see GameToolExecutor._execute_sql/_execute_soap_command)
for an after-the-fact audit trail, since nothing here asks for
confirmation before acting.
"""

import json
import logging
import urllib.error
import urllib.request

from guide_soap import SoapError, call_soap_command

logger = logging.getLogger(__name__)


ADMIN_TOOLS = [
    {
        "name": "execute_sql",
        "description": (
            "Execute a raw SQL statement against a live AzerothCore "
            "database (world, characters, or auth). No restrictions: "
            "SELECT/INSERT/UPDATE/DELETE/ALTER all run for real, "
            "immediately, with no confirmation and no undo. Use exact "
            "table/column names — this server may be mid-migration on "
            "some renamed columns (e.g. creature.id vs creature.id1); "
            "check information_schema first if unsure. Returns affected "
            "row count for writes, or up to 50 rows for SELECT."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "database": {
                    "type": "string",
                    "enum": ["world", "characters", "auth", "playerbots"],
                    "description": "Which AzerothCore database to run the statement against.",
                },
                "sql": {
                    "type": "string",
                    "description": "The exact SQL statement to execute.",
                },
            },
            "required": ["database", "sql"],
        },
    },
    {
        "name": "execute_soap_command",
        "description": (
            "Run a worldserver console/GM command via SOAP, exactly as "
            "if typed at the console or in-game with a leading dot "
            "(e.g. 'kick Grimjaw', 'character erase Grimjaw', 'server "
            "shutdown 60', 'additem 6948 1', 'modify money 10000'). No "
            "allowlist: any valid console command runs, including "
            "destructive ones (kick/ban/erase/shutdown). Do not run "
            "destructive commands unless the request clearly calls for "
            "them. Returns the command's raw console output."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The command text with no leading dot.",
                },
            },
            "required": ["command"],
        },
    },
    {
        "name": "web_search",
        "description": (
            "Search the live web for information not in this server's "
            "database — community knowledge, best-in-slot/theorycrafting "
            "opinions, patch notes, wiki pages, anything requiring "
            "outside sources (e.g. 'best weapon from Deadmines for a "
            "level 20 warrior'). Costs a small per-search fee, so only "
            "call this when the local game-data tools genuinely can't "
            "answer the question. Returns a short synthesized answer "
            "plus source URLs, not raw search results."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query.",
                },
            },
            "required": ["query"],
        },
    },
]


_ADMIN_DB_DEFAULTS = {
    "world": "acore_world",
    "characters": "acore_characters",
    "auth": "acore_auth",
    "playerbots": "acore_playerbots",
}


class AdminToolMixin:
    """Mixin providing GM Admin Mode tool execution.

    Only ever exercised when `self.admin_tools` (set by the bridge for
    the duration of one admin request) is non-empty — see
    GameToolExecutor._execute_tool's definition lookup.
    """

    def _admin_database_name(self, key: str) -> str:
        configured = getattr(self, 'admin_db_names', {}).get(key)
        return configured or _ADMIN_DB_DEFAULTS[key]

    def _execute_sql(self, params: dict) -> str:
        database = params.get('database', '')
        sql = (params.get('sql') or '').strip()
        if database not in _ADMIN_DB_DEFAULTS:
            return f"Unknown database: {database!r}"
        if not sql:
            return "Empty SQL statement."

        logger.warning(
            "ADMIN SQL [%s]: %s", database, sql
        )

        import mysql.connector
        conn_config = self.db_config.copy()
        conn_config['database'] = self._admin_database_name(database)
        conn = mysql.connector.connect(**conn_config)
        try:
            cursor = conn.cursor()
            try:
                cursor.execute(sql)
                if cursor.with_rows:
                    columns = [d[0] for d in cursor.description]
                    rows = cursor.fetchmany(50)
                    result = {
                        'columns': columns,
                        'rows': rows,
                        'truncated_to': 50,
                    }
                    conn.commit()
                    return str(result)
                conn.commit()
                return f"OK. {cursor.rowcount} row(s) affected."
            finally:
                cursor.close()
        except Exception as exc:
            conn.rollback()
            logger.error("ADMIN SQL failed: %s", exc)
            return f"SQL error: {exc}"
        finally:
            conn.close()

    def _execute_soap_command(self, params: dict) -> str:
        command = (params.get('command') or '').strip()
        if not command:
            return "Empty command."
        command = command.lstrip('.').strip()

        soap_config = getattr(self, 'soap_config', None) or {}
        host = soap_config.get('host')
        port = soap_config.get('port')
        username = soap_config.get('username')
        password = soap_config.get('password')
        if not (host and port and username and password):
            return (
                "SOAP is not configured on this bridge "
                "(LLMGuide.Soap.Host/Port/Username/Password)."
            )

        logger.warning("ADMIN SOAP: %s", command)

        try:
            return call_soap_command(
                host, int(port), username, password, command
            )
        except SoapError as exc:
            return f"SOAP fault: {exc}"
        except Exception as exc:
            logger.error("ADMIN SOAP failed: %s", exc)
            return f"SOAP transport error: {exc}"

    def _execute_web_search(self, params: dict) -> str:
        query = (params.get('query') or '').strip()
        if not query:
            return "Empty query."

        config = getattr(self, 'web_search_config', None) or {}
        api_key = config.get('api_key')
        model = config.get('model')
        base_url = config.get('base_url') or 'https://openrouter.ai/api/v1'
        if not (api_key and model):
            return (
                "Web search is not configured on this bridge "
                "(reuses LLMGuide.OpenRouter.ApiKey/Model; provider "
                "must be openrouter)."
            )

        logger.warning("ADMIN WEB SEARCH: %s", query)

        # A separate, self-contained OpenRouter call using its Exa-backed
        # `web` plugin — deliberately not woven into the main tool-calling
        # loop (that plugin is a per-request feature that always searches,
        # not something the model can choose per-round). Wrapping it as a
        # tool call instead means a search only happens when the model
        # actually decides this question needs one.
        body = json.dumps({
            'model': model,
            'messages': [{
                'role': 'user',
                'content': (
                    'Answer concisely using web search results, WoW '
                    f'3.3.5a (Wrath of the Lich King) context: {query}'
                ),
            }],
            'plugins': [{'id': 'web', 'max_results': 5}],
            # Real USD cost for this exact call, not an estimate —
            # accumulated into self.extra_cost_usd so the bridge's
            # per-request cost log includes it.
            'usage': {'include': True},
        }).encode('utf-8')

        req = urllib.request.Request(
            base_url.rstrip('/') + '/chat/completions',
            data=body,
            headers={
                'Content-Type': 'application/json',
                'Authorization': f'Bearer {api_key}',
            },
            method='POST',
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                payload = json.loads(resp.read().decode('utf-8'))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode('utf-8', 'replace')
            logger.error("ADMIN WEB SEARCH HTTP error: %s", detail)
            return f"Web search HTTP error: {exc.code} {detail[:300]}"
        except Exception as exc:
            logger.error("ADMIN WEB SEARCH failed: %s", exc)
            return f"Web search error: {exc}"

        cost = (payload.get('usage') or {}).get('cost')
        if cost is not None:
            self.extra_cost_usd = (self.extra_cost_usd or 0.0) + float(cost)

        try:
            message = payload['choices'][0]['message']
            text = (message.get('content') or '').strip()
            urls = [
                ann['url_citation']['url']
                for ann in (message.get('annotations') or [])
                if ann.get('type') == 'url_citation'
                and ann.get('url_citation', {}).get('url')
            ]
        except (KeyError, IndexError, TypeError) as exc:
            logger.error("ADMIN WEB SEARCH unexpected payload: %s", exc)
            return f"Web search returned an unexpected response: {payload}"

        if urls:
            text += "\nSources: " + ", ".join(dict.fromkeys(urls))
        return text or "Web search returned no answer."
