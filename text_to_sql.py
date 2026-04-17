import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple
from urllib.parse import quote_plus

import pandas as pd
from sqlalchemy import MetaData, Table, create_engine, inspect, select, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import SQLAlchemyError

from llm_providers import LLMProvider


_SQL_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|pragma|attach|detach|truncate)\b",
    re.IGNORECASE,
)
_QUESTION_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "by",
    "for",
    "from",
    "how",
    "in",
    "is",
    "last",
    "list",
    "many",
    "month",
    "of",
    "on",
    "or",
    "show",
    "the",
    "this",
    "to",
    "top",
    "what",
    "which",
    "with",
    "year",
}


def _sanitize_column(name: str, used: set[str]) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]+", "_", name or "").strip("_").lower()
    cleaned = cleaned or "col"
    candidate = cleaned
    counter = 2
    while candidate in used:
        candidate = f"{cleaned}_{counter}"
        counter += 1
    used.add(candidate)
    return candidate


def _dtype_to_sql(dtype) -> str:
    if pd.api.types.is_integer_dtype(dtype):
        return "INTEGER"
    if pd.api.types.is_float_dtype(dtype):
        return "REAL"
    if pd.api.types.is_bool_dtype(dtype):
        return "INTEGER"
    if pd.api.types.is_datetime64_any_dtype(dtype):
        return "TEXT"
    return "TEXT"


def _extract_sql(text: str) -> str:
    if not text:
        return ""
    fenced = re.search(r"```(?:sql)?\s*(.*?)```", text, re.IGNORECASE | re.DOTALL)
    if fenced:
        return fenced.group(1).strip()
    if "sql:" in text.lower():
        parts = re.split(r"sql:\s*", text, flags=re.IGNORECASE)
        return parts[-1].strip()
    return text.strip()


def _ensure_select_only(sql: str) -> None:
    normalized = sql.strip().lower()
    if not (normalized.startswith("select") or normalized.startswith("with")):
        raise ValueError("Generated SQL must start with SELECT or WITH.")
    if _SQL_FORBIDDEN.search(sql):
        raise ValueError("Generated SQL contains forbidden statements.")


def _apply_limit(sql: str, max_rows: int) -> str:
    if re.search(r"\blimit\b", sql, re.IGNORECASE):
        return sql
    return f"{sql.rstrip(';')} LIMIT {max_rows}"


def _identifier_terms(value: str) -> set[str]:
    if not value:
        return set()

    expanded = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", value)
    base_tokens = [
        token.lower()
        for token in re.split(r"[^A-Za-z0-9]+", expanded)
        if token and len(token) > 1
    ]
    terms: set[str] = set(base_tokens)
    for token in base_tokens:
        if token.endswith("s") and len(token) > 3:
            terms.add(token[:-1])
    return terms


def _question_terms(question: str) -> set[str]:
    return {term for term in _identifier_terms(question) if term not in _QUESTION_STOPWORDS}


def _table_relevance_score(
    *,
    question_terms: set[str],
    question_text: str,
    table_name: str,
    schema: List[Tuple[str, str]],
) -> int:
    if not question_terms:
        return 0

    question_lower = (question_text or "").lower()
    score = 0
    table_terms = _identifier_terms(table_name)
    matched_table_terms = table_terms.intersection(question_terms)
    score += len(matched_table_terms) * 8

    if table_name and table_name.lower() in question_lower:
        score += 12

    matched_columns = 0
    for column_name, _dtype in schema:
        column_terms = _identifier_terms(column_name)
        overlap = column_terms.intersection(question_terms)
        if overlap:
            matched_columns += 1
            score += 2 + len(overlap) * 3

    # Light boost for coverage so table candidates with multiple relevant columns surface higher.
    score += min(matched_columns, 5)
    return score


def _extract_string_list(text: str) -> List[str]:
    if not text:
        return []

    candidate = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", text, re.IGNORECASE | re.DOTALL)
    if fenced:
        candidate = fenced.group(1).strip()
    else:
        bracketed = re.search(r"\[(?:.|\n|\r)*\]", text)
        if bracketed:
            candidate = bracketed.group(0).strip()

    try:
        parsed = json.loads(candidate)
        if isinstance(parsed, list):
            return [str(item).strip() for item in parsed if str(item).strip()]
    except Exception:
        pass

    values: List[str] = []
    for line in re.split(r"[\n,;]+", text):
        cleaned = line.strip().strip("-*").strip().strip('"').strip("'")
        if cleaned:
            values.append(cleaned)
    return values


def _connection_display_name(connection_uri: str) -> str:
    try:
        url = make_url(connection_uri)
        db_name = (url.database or "").strip("/")
        if url.host:
            suffix = f"/{db_name}" if db_name else ""
            return f"{url.drivername}://{url.host}{suffix}"
        if url.drivername.startswith("sqlite"):
            return f"{url.drivername}:///{db_name}"
        return url.render_as_string(hide_password=True)
    except Exception:
        return "database"


@dataclass
class StructuredTable:
    df: pd.DataFrame
    table_name: str
    column_mapping: Dict[str, str]
    schema: List[Tuple[str, str]]
    sample_rows: List[Dict[str, object]]


@dataclass
class DatabaseTable:
    name: str
    schema: List[Tuple[str, str]]
    sample_rows: List[Dict[str, object]]


class StructuredDataProcessor:
    SUPPORTED_EXTENSIONS = {".csv", ".xlsx"}

    @staticmethod
    def load_table(file_path: Path) -> StructuredTable:
        suffix = file_path.suffix.lower()
        if suffix not in StructuredDataProcessor.SUPPORTED_EXTENSIONS:
            raise ValueError(
                "Unsupported structured file type. Use CSV or Excel (XLSX)."
            )

        if suffix == ".csv":
            df = pd.read_csv(file_path)
        else:
            df = pd.read_excel(file_path)

        if df.empty:
            raise ValueError("Structured file has no rows.")

        used = set()
        mapping: Dict[str, str] = {}
        for col in df.columns:
            mapping[str(col)] = _sanitize_column(str(col), used)

        df = df.rename(columns=mapping)

        schema = [(col, _dtype_to_sql(dtype)) for col, dtype in df.dtypes.items()]
        sample_rows = df.head(5).fillna("").to_dict(orient="records")

        return StructuredTable(
            df=df,
            table_name="data",
            column_mapping=mapping,
            schema=schema,
            sample_rows=sample_rows,
        )


class DatabaseDataProcessor:
    SUPPORTED_URI_PREFIXES = (
        "sqlite:///",
        "postgresql://",
        "postgresql+psycopg2://",
        "mysql://",
        "mysql+pymysql://",
    )

    @staticmethod
    def parse_include_tables(include_tables: str | None) -> List[str]:
        if not include_tables:
            return []
        return [name.strip() for name in include_tables.split(",") if name.strip()]

    @staticmethod
    def build_connection_uri(
        *,
        db_type: str,
        host: str,
        port: str,
        database: str,
        username: str,
        password: str,
    ) -> str:
        normalized_type = (db_type or "").strip().lower()
        if normalized_type in {"postgres", "postgresql"}:
            driver = "postgresql+psycopg2"
            default_port = "5432"
        elif normalized_type in {"mysql"}:
            driver = "mysql+pymysql"
            default_port = "3306"
        else:
            raise ValueError("db_type must be either 'postgresql' or 'mysql'.")

        host_value = (host or "").strip()
        db_value = (database or "").strip()
        user_value = (username or "").strip()
        password_value = (password or "").strip()
        port_value = (port or "").strip() or default_port

        if not host_value:
            raise ValueError("Database host is required.")
        if not db_value:
            raise ValueError("Database name is required.")
        if not user_value:
            raise ValueError("Database username is required.")
        if not password_value:
            raise ValueError("Database password is required.")
        if not port_value.isdigit():
            raise ValueError("Database port must be a valid number.")

        safe_user = quote_plus(user_value)
        safe_password = quote_plus(password_value)
        return (
            f"{driver}://{safe_user}:{safe_password}"
            f"@{host_value}:{port_value}/{db_value}"
        )

    @staticmethod
    def validate_connection_uri(connection_uri: str) -> str:
        cleaned = (connection_uri or "").strip()
        if not cleaned:
            raise ValueError("Database connection URI is required.")
        if not cleaned.startswith(DatabaseDataProcessor.SUPPORTED_URI_PREFIXES):
            allowed = ", ".join(DatabaseDataProcessor.SUPPORTED_URI_PREFIXES)
            raise ValueError(
                "Unsupported database URI. Use one of these URI prefixes: " + allowed
            )
        return cleaned

    @staticmethod
    def load_database_catalog(
        connection_uri: str,
        include_tables: List[str] | None = None,
    ) -> tuple[Engine, List[str], Dict[str, List[Tuple[str, str]]], List[str]]:
        uri = DatabaseDataProcessor.validate_connection_uri(connection_uri)
        warnings: List[str] = []
        engine = create_engine(uri)

        try:
            inspector = inspect(engine)
            all_tables = inspector.get_table_names()
            if not all_tables:
                raise ValueError("No tables were found in the connected database.")

            selected_tables = include_tables or all_tables
            missing = sorted(set(selected_tables) - set(all_tables))
            if missing:
                warnings.append(
                    "Ignored missing tables: " + ", ".join(missing)
                )
                selected_tables = [name for name in selected_tables if name in all_tables]

            if not selected_tables:
                raise ValueError("No valid tables available for Text-to-SQL.")

            schema_by_table: Dict[str, List[Tuple[str, str]]] = {}
            for table_name in selected_tables:
                columns = inspector.get_columns(table_name)
                schema_by_table[table_name] = [
                    (str(column.get("name", "")), str(column.get("type", "TEXT")))
                    for column in columns
                ]

            return engine, selected_tables, schema_by_table, warnings
        except SQLAlchemyError as exc:
            engine.dispose()
            raise ValueError(f"Database connection/query failed: {exc}") from exc
        except Exception:
            engine.dispose()
            raise

    @staticmethod
    def load_database_tables_with_samples(
        *,
        engine: Engine,
        table_names: List[str],
        schema_by_table: Dict[str, List[Tuple[str, str]]],
        sample_rows_per_table: int = 3,
    ) -> List[DatabaseTable]:
        metadata = MetaData()
        tables: List[DatabaseTable] = []
        try:
            with engine.connect() as conn:
                for table_name in table_names:
                    schema = schema_by_table.get(table_name, [])
                    table_obj = Table(table_name, metadata, autoload_with=engine)
                    query = select(table_obj).limit(sample_rows_per_table)
                    result = conn.execute(query)
                    sample_rows = [dict(row._mapping) for row in result.fetchall()]

                    tables.append(
                        DatabaseTable(
                            name=table_name,
                            schema=schema,
                            sample_rows=sample_rows,
                        )
                    )
            return tables
        except SQLAlchemyError as exc:
            raise ValueError(f"Database table sampling failed: {exc}") from exc

    @staticmethod
    def load_database_context(
        connection_uri: str,
        include_tables: List[str] | None = None,
        *,
        question: str = "",
        max_tables: int = 20,
        sample_rows_per_table: int = 3,
    ) -> tuple[Engine, List[DatabaseTable], List[str]]:
        engine, selected_tables, schema_by_table, warnings = DatabaseDataProcessor.load_database_catalog(
            connection_uri=connection_uri,
            include_tables=include_tables,
        )

        terms = _question_terms(question)
        if len(selected_tables) > max_tables:
            ranked_candidates = []
            for idx, table_name in enumerate(selected_tables):
                score = _table_relevance_score(
                    question_terms=terms,
                    question_text=question,
                    table_name=table_name,
                    schema=schema_by_table.get(table_name, []),
                )
                ranked_candidates.append((score, idx, table_name))

            if any(score > 0 for score, _idx, _name in ranked_candidates):
                ranked_candidates.sort(key=lambda item: (-item[0], item[1]))
                selected_tables = [name for _score, _idx, name in ranked_candidates[:max_tables]]
                warnings.append(
                    f"Selected top {max_tables} of {len(ranked_candidates)} tables by "
                    "question/schema relevance for prompt size."
                )
            else:
                selected_tables = selected_tables[:max_tables]
                warnings.append(
                    f"No clear table-name/column-name matches found in the question. "
                    f"Falling back to first {max_tables} tables."
                )

        tables = DatabaseDataProcessor.load_database_tables_with_samples(
            engine=engine,
            table_names=selected_tables,
            schema_by_table=schema_by_table,
            sample_rows_per_table=sample_rows_per_table,
        )
        return engine, tables, warnings


class TextToSQLService:
    DB_PROMPT_TABLE_LIMIT = 20
    DB_ROUTER_POOL_LIMIT = 40
    DB_ROUTER_COLUMN_PREVIEW_LIMIT = 16

    def __init__(self, llm_provider: LLMProvider, model: str):
        self.llm_provider = llm_provider
        self.model = model

    def _route_database_tables(
        self,
        *,
        question: str,
        candidate_tables: List[str],
        schema_by_table: Dict[str, List[Tuple[str, str]]],
        api_key: str,
        max_tables: int,
    ) -> List[str]:
        if not candidate_tables:
            return []

        table_lines = []
        for table_name in candidate_tables:
            schema = schema_by_table.get(table_name, [])
            preview = ", ".join(
                column_name
                for column_name, _dtype in schema[: self.DB_ROUTER_COLUMN_PREVIEW_LIMIT]
            )
            extra = ""
            if len(schema) > self.DB_ROUTER_COLUMN_PREVIEW_LIMIT:
                extra = f", ... (+{len(schema) - self.DB_ROUTER_COLUMN_PREVIEW_LIMIT} more)"
            table_lines.append(f"- {table_name}: {preview}{extra}")

        prompt = (
            "You are a SQL query planner. Choose the minimum required table names to answer "
            "the question.\n\n"
            f"Question: {question}\n\n"
            "Candidate tables (name and key columns):\n"
            f"{chr(10).join(table_lines)}\n\n"
            "Rules:\n"
            f"- Return only a JSON array of table names, max {max_tables} items.\n"
            "- Use exact table names from the candidate list.\n"
            "- Prefer fewer, high-confidence tables.\n"
            "- No markdown, no explanation.\n\n"
            "JSON:"
        )

        response = self.llm_provider.complete(
            prompt=prompt,
            model=self.model,
            api_key=api_key,
            temperature=0.0,
            max_tokens=350,
        )
        raw_selected = _extract_string_list(response)
        if not raw_selected:
            return []

        exact_lookup = {name: name for name in candidate_tables}
        lower_lookup = {name.lower(): name for name in candidate_tables}
        selected: List[str] = []
        for candidate in raw_selected:
            normalized = candidate.strip().strip('"').strip("'")
            if not normalized:
                continue
            matched = exact_lookup.get(normalized) or lower_lookup.get(normalized.lower())
            if matched and matched not in selected:
                selected.append(matched)

        if not selected:
            response_lower = (response or "").lower()
            for table_name in candidate_tables:
                pattern = rf"\b{re.escape(table_name.lower())}\b"
                if re.search(pattern, response_lower) and table_name not in selected:
                    selected.append(table_name)

        return selected[:max_tables]

    def _select_tables_for_database_prompt(
        self,
        *,
        question: str,
        available_tables: List[str],
        schema_by_table: Dict[str, List[Tuple[str, str]]],
        api_key: str,
    ) -> tuple[List[str], List[str]]:
        warnings: List[str] = []
        if len(available_tables) <= self.DB_PROMPT_TABLE_LIMIT:
            return available_tables, warnings

        terms = _question_terms(question)
        ranked: List[Tuple[int, int, str]] = []
        for idx, table_name in enumerate(available_tables):
            score = _table_relevance_score(
                question_terms=terms,
                question_text=question,
                table_name=table_name,
                schema=schema_by_table.get(table_name, []),
            )
            ranked.append((score, idx, table_name))

        if any(score > 0 for score, _idx, _name in ranked):
            ranked.sort(key=lambda item: (-item[0], item[1]))
            router_pool = [name for _score, _idx, name in ranked[: self.DB_ROUTER_POOL_LIMIT]]
            warnings.append(
                f"Heuristically narrowed table routing pool to {len(router_pool)} of "
                f"{len(available_tables)} tables."
            )
        else:
            router_pool = available_tables[: self.DB_ROUTER_POOL_LIMIT]
            warnings.append(
                f"No clear table/column keyword matches in question; router used first "
                f"{len(router_pool)} of {len(available_tables)} tables."
            )

        if len(router_pool) <= self.DB_PROMPT_TABLE_LIMIT:
            return router_pool, warnings

        routed_tables: List[str] = []
        try:
            routed_tables = self._route_database_tables(
                question=question,
                candidate_tables=router_pool,
                schema_by_table=schema_by_table,
                api_key=api_key,
                max_tables=self.DB_PROMPT_TABLE_LIMIT,
            )
        except Exception:
            routed_tables = []

        if routed_tables:
            warnings.append(
                f"Router selected {len(routed_tables)} table(s) from {len(router_pool)} candidates."
            )
            return routed_tables, warnings

        warnings.append(
            f"Router did not return valid table names; using top {self.DB_PROMPT_TABLE_LIMIT} "
            "heuristic tables."
        )
        return router_pool[: self.DB_PROMPT_TABLE_LIMIT], warnings

    def answer_question(
        self,
        *,
        question: str,
        api_key: str,
        file_path: Path | None = None,
        connection_uri: str | None = None,
        include_tables: List[str] | None = None,
        max_rows: int = 200,
    ) -> Dict[str, object]:
        if file_path and connection_uri:
            raise ValueError("Provide either file upload or database connection, not both.")
        if not file_path and not connection_uri:
            raise ValueError("Provide a file or a database connection URI.")

        if file_path:
            return self._answer_from_file(
                file_path=file_path,
                question=question,
                api_key=api_key,
                max_rows=max_rows,
            )

        return self._answer_from_database(
            connection_uri=connection_uri or "",
            include_tables=include_tables or [],
            question=question,
            api_key=api_key,
            max_rows=max_rows,
        )

    def _answer_from_file(
        self,
        *,
        file_path: Path,
        question: str,
        api_key: str,
        max_rows: int,
    ) -> Dict[str, object]:
        table = StructuredDataProcessor.load_table(file_path)
        conn = sqlite3.connect(":memory:")
        try:
            table.df.to_sql(table.table_name, conn, index=False)
            sql = self._generate_sql(
                question=question,
                schema=table.schema,
                column_mapping=table.column_mapping,
                table_name=table.table_name,
                sample_rows=table.sample_rows,
                api_key=api_key,
            )
            sql = _apply_limit(sql, max_rows)
            _ensure_select_only(sql)
            rows, columns = self._run_query_sqlite(conn, sql, max_rows=max_rows)
            answer = self._summarize_answer(
                question=question,
                sql=sql,
                rows=rows,
                api_key=api_key,
            )
            return {
                "question": question,
                "sql": sql,
                "answer": answer,
                "columns": columns,
                "rows": rows,
                "row_count": len(rows),
                "column_mapping": table.column_mapping,
                "warnings": [],
                "source_type": "file",
                "source_name": file_path.name,
                "tables": [table.table_name],
            }
        finally:
            conn.close()

    def _answer_from_database(
        self,
        *,
        connection_uri: str,
        include_tables: List[str],
        question: str,
        api_key: str,
        max_rows: int,
    ) -> Dict[str, object]:
        engine, available_tables, schema_by_table, warnings = DatabaseDataProcessor.load_database_catalog(
            connection_uri=connection_uri,
            include_tables=include_tables,
        )

        try:
            selected_table_names, routing_warnings = self._select_tables_for_database_prompt(
                question=question,
                available_tables=available_tables,
                schema_by_table=schema_by_table,
                api_key=api_key,
            )
            warnings.extend(routing_warnings)

            tables = DatabaseDataProcessor.load_database_tables_with_samples(
                engine=engine,
                table_names=selected_table_names,
                schema_by_table=schema_by_table,
            )

            sql = self._generate_sql_for_database(
                question=question,
                tables=tables,
                api_key=api_key,
            )
            sql = _apply_limit(sql, max_rows)
            _ensure_select_only(sql)
            rows, columns = self._run_query_engine(engine, sql, max_rows=max_rows)
            answer = self._summarize_answer(
                question=question,
                sql=sql,
                rows=rows,
                api_key=api_key,
            )
            table_names = [table.name for table in tables]
            return {
                "question": question,
                "sql": sql,
                "answer": answer,
                "columns": columns,
                "rows": rows,
                "row_count": len(rows),
                "column_mapping": {},
                "warnings": warnings,
                "source_type": "database",
                "source_name": _connection_display_name(connection_uri),
                "tables": table_names,
            }
        finally:
            engine.dispose()

   def _generate_sql(
        self,
        *,
        question: str,
        schema: List[Tuple[str, str]],
        column_mapping: Dict[str, str],
        table_name: str,
        sample_rows: List[Dict[str, object]],
        api_key: str,
    ) -> str:
        schema_lines = "\n".join(f"- {name} ({dtype})" for name, dtype in schema)
        mapping_lines = "\n".join(
            f"- {original} -> {sanitized}" for original, sanitized in column_mapping.items()
        )
        
        # --- NEW CSV CONVERSION ---
        if sample_rows:
            sample_csv_str = pd.DataFrame(sample_rows).to_csv(index=False).strip()
        else:
            sample_csv_str = "(No sample data)"

        prompt = (
            "You are a data analyst. Convert the question into a single SQLite SELECT "
            "query using only the provided schema. Do not use any write operations.\n\n"
            f"Table: {table_name}\nColumns:\n{schema_lines}\n\n"
            f"Column name mapping (original -> sanitized):\n{mapping_lines}\n\n"
            "Sample rows (CSV format):\n"
            f"{sample_csv_str}\n\n"
            "Rules:\n"
            "- Output only SQL, no markdown, no explanations.\n"
            "- Use the table name exactly as provided.\n"
            "- If filtering by text, use LIKE with % wildcards.\n"
            "- IMPORTANT: If the question implies counting, totaling, or averaging, ALWAYS use SQL aggregations (COUNT, SUM, AVG) so the query returns a single numerical result rather than raw rows.\n\n"
            f"Question: {question}\nSQL:"
        )
        response = self.llm_provider.complete(
            prompt=prompt,
            model=self.model,
            api_key=api_key,
            temperature=0.0,
            max_tokens=500,
        )
        return _extract_sql(response)

    @staticmethod
    def _run_query_sqlite(
        conn: sqlite3.Connection, sql: str, max_rows: int
    ) -> tuple[List[Dict[str, object]], List[str]]:
        cursor = conn.cursor()
        cursor.execute(sql)
        columns = [desc[0] for desc in cursor.description or []]
        raw_rows = cursor.fetchmany(max_rows)
        return [dict(zip(columns, row)) for row in raw_rows], columns

   def _generate_sql_for_database(
        self,
        *,
        question: str,
        tables: List[DatabaseTable],
        api_key: str,
    ) -> str:
        schema_lines = []
        sample_csv_lines = []
        
        for table in tables:
            columns = ", ".join(f"{name} ({dtype})" for name, dtype in table.schema)
            schema_lines.append(f"- {table.name}: {columns}")
            
            # --- NEW CSV CONVERSION ---
            if table.sample_rows:
                # Convert the list of dicts to a highly compressed CSV string
                csv_string = pd.DataFrame(table.sample_rows).to_csv(index=False).strip()
                sample_csv_lines.append(f"Table: {table.name}\n{csv_string}\n")
            else:
                sample_csv_lines.append(f"Table: {table.name}\n(No sample data)\n")

        sample_payload_str = "\n".join(sample_csv_lines)

        prompt = (
            "You are a data analyst. Convert the question into a single SQL query.\n\n"
            "Database tables and columns:\n"
            f"{chr(10).join(schema_lines)}\n\n"
            "Sample rows per table (CSV format):\n"
            f"{sample_payload_str}\n\n"
            "Rules:\n"
            "- Output only SQL, no markdown, no explanations.\n"
            "- Use only tables and columns listed above.\n"
            "- Generate a read-only query (SELECT or WITH ... SELECT).\n"
            "- If filtering text, use LIKE with % wildcards.\n"
            "- IMPORTANT: If the question implies counting, totaling, or averaging, ALWAYS use SQL aggregations (COUNT, SUM, AVG) so the query returns a single numerical result rather than raw rows.\n\n"
            f"Question: {question}\nSQL:"
        )
        response = self.llm_provider.complete(
            prompt=prompt,
            model=self.model,
            api_key=api_key,
            temperature=0.0,
            max_tokens=650,
        )
        return _extract_sql(response)

    @staticmethod
    def _run_query_engine(
        engine: Engine,
        sql: str,
        max_rows: int,
    ) -> tuple[List[Dict[str, object]], List[str]]:
        with engine.connect() as conn:
            result = conn.execute(text(sql))
            columns = list(result.keys())
            raw_rows = result.fetchmany(max_rows)
            return [dict(row._mapping) for row in raw_rows], columns

    def _summarize_answer(
        self,
        *,
        question: str,
        sql: str,
        rows: List[Dict[str, object]],
        api_key: str,
    ) -> str:
        prompt = (
            "You are a helpful data assistant. Answer the question using the SQL result.\n\n"
            f"Question: {question}\n"
            f"SQL: {sql}\n"
            f"Rows (JSON): {json.dumps(rows, ensure_ascii=False)}\n\n"
            "Answer concisely in plain text."
        )
        response = self.llm_provider.complete(
            prompt=prompt,
            model=self.model,
            api_key=api_key,
            temperature=0.2,
            max_tokens=400,
        )
        return response.strip() or "No answer generated."
