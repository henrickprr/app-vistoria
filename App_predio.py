"""PWA de vistoria e auditoria para engenharia civil.

Compatibilidade validada:
    - Flet 0.86.5
    - fpdf2 2.8.8

Execucao local:
    python -m pip install "flet==0.86.5" "fpdf2==2.8.8"
    flet run App_predio.py

Execucao web/PWA:
    flet run --web App_predio.py

O arquivo concentra a aplicacao por solicitacao do projeto, mas separa as
responsabilidades em funcoes puras, repositorio Firebase, gerador de PDF e UI.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import hmac
import json
import os
import re
import secrets
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import Any

import flet as ft
from fpdf import FPDF
from fpdf.enums import XPos, YPos

FIREBASE_URL = os.getenv(
    "FIREBASE_URL",
    "https://app-vistoria-986c3-default-rtdb.firebaseio.com/banco_dados.json",
)
FIREBASE_AUTH_TOKEN = os.getenv("FIREBASE_AUTH_TOKEN", "").strip()
FIREBASE_TIMEOUT_SECONDS = 20

SESSION_USER_KEY = "app_vistoria.usuario"

PERFIS = {
    "admin": "Administrador",
    "editor": "Editor",
    "visualizador": "Visualizador",
}

STATUS = (
    "Não Iniciado",
    "Em Andamento",
    "Finalizado",
    "Não Conforme",
    "Existente",
)

STATUS_PDF_RGB = {
    "Não Iniciado": (189, 189, 189),
    "Em Andamento": (33, 150, 243),
    "Finalizado": (76, 175, 80),
    "Não Conforme": (244, 67, 54),
    "Existente": (255, 152, 0),
}

STATUS_FLET_COLOR = {
    "Não Iniciado": ft.Colors.GREY_500,
    "Em Andamento": ft.Colors.BLUE_500,
    "Finalizado": ft.Colors.GREEN_500,
    "Não Conforme": ft.Colors.RED_500,
    "Existente": ft.Colors.ORANGE_500,
}

SERVICOS_BASE = (
    "Revestimento piso banheiro",
    "Dreno",
    "Revestimento porcelanato",
    "Limpeza",
    "Forro de gesso",
    "Forro da varanda",
    "Ralos da varanda",
    "Forro do banheiro",
    "Piso acabado",
)

CHAVES_FIREBASE_PROIBIDAS = re.compile(r"[.#$\[\]/]")


# ---------------------------------------------------------------------------
# Normalizacao e modelo de dados
# ---------------------------------------------------------------------------


def validar_chave_firebase(valor: str, rotulo: str = "Nome") -> str:
    """Valida nomes usados como chaves do Realtime Database."""

    texto = str(valor or "").strip()
    if not texto:
        raise ValueError(f"{rotulo} não pode ficar vazio.")
    if CHAVES_FIREBASE_PROIBIDAS.search(texto):
        raise ValueError(f"{rotulo} contém caractere inválido. Não use . # $ [ ] ou /.")
    return texto


def chave_ordenacao_natural(valor: Any) -> tuple[Any, ...]:
    partes = re.split(r"(\d+)", str(valor).casefold())
    return tuple(int(p) if p.isdigit() else p for p in partes)


def converter_listas_para_dicionarios(objeto: Any) -> Any:
    """Converte listas acidentais do Firebase em dicionarios indexados.

    O Realtime Database pode devolver uma lista quando todas as chaves de um
    no parecem indices numericos consecutivos. Na hierarquia de obras isso e
    perigoso, pois o restante da aplicacao espera dicionarios. A conversao e
    recursiva e remove apenas posicoes nulas criadas pelo Firebase.
    """

    if isinstance(objeto, list):
        return {
            str(indice): converter_listas_para_dicionarios(valor)
            for indice, valor in enumerate(objeto)
            if valor is not None
        }
    if isinstance(objeto, dict):
        return {
            str(chave): converter_listas_para_dicionarios(valor)
            for chave, valor in objeto.items()
        }
    return objeto


def _possui_lista_estrutural(objeto: Any, caminho: tuple[str, ...] = ()) -> bool:
    """Detecta arrays indevidos, ignorando a lista legitima de historico."""

    if isinstance(objeto, list):
        return caminho != ("historico",)
    if isinstance(objeto, dict):
        return any(
            _possui_lista_estrutural(valor, caminho + (str(chave),))
            for chave, valor in objeto.items()
            if caminho or str(chave) != "historico"
        )
    return False


def _observacao_segura(valor: Any) -> str:
    if valor is None:
        return ""
    if isinstance(valor, str):
        return valor
    try:
        return json.dumps(valor, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        return str(valor)


def _dados_atividade_seguros(valor: Any) -> dict[str, str]:
    if isinstance(valor, str) and valor in STATUS:
        return {"status": valor, "obs": ""}
    if not isinstance(valor, dict):
        return {"status": "Não Iniciado", "obs": ""}

    status = str(valor.get("status", "Não Iniciado"))
    if status not in STATUS:
        status = "Não Iniciado"
    return {
        "status": status,
        "obs": _observacao_segura(valor.get("obs", "")),
    }


def criar_hash_senha(senha: str) -> dict[str, Any]:
    """Gera PBKDF2 para novos usuarios sem quebrar senhas legadas."""

    iteracoes = 310_000
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", senha.encode("utf-8"), bytes.fromhex(salt), iteracoes
    ).hex()
    return {
        "senha_hash": digest,
        "senha_salt": salt,
        "senha_iteracoes": iteracoes,
    }


def verificar_senha(registro_usuario: dict[str, Any], senha: str) -> bool:
    """Aceita PBKDF2 e, por compatibilidade, o antigo campo ``senha``."""

    digest_salvo = registro_usuario.get("senha_hash")
    salt = registro_usuario.get("senha_salt")
    if digest_salvo and salt:
        try:
            iteracoes = int(registro_usuario.get("senha_iteracoes", 310_000))
            digest = hashlib.pbkdf2_hmac(
                "sha256", senha.encode("utf-8"), bytes.fromhex(str(salt)), iteracoes
            ).hex()
            return hmac.compare_digest(str(digest_salvo), digest)
        except (TypeError, ValueError):
            return False

    # Compatibilidade com o banco existente. Novos cadastros nunca usam texto puro.
    senha_legada = registro_usuario.get("senha")
    return senha_legada is not None and hmac.compare_digest(
        str(senha_legada), str(senha)
    )


def usuario_admin_padrao() -> dict[str, Any]:
    return {
        **criar_hash_senha("123"),
        "perfil": "admin",
        "nome": "Admin Principal",
    }


def banco_padrao() -> dict[str, Any]:
    return {
        "obras": {},
        "usuarios": {"admin": usuario_admin_padrao()},
        "historico": [],
    }


def normalizar_banco(dados_brutos: Any) -> tuple[dict[str, Any], bool]:
    """Normaliza todo o banco sem descartar andares ou historico recuperaveis.

    Retorna ``(banco, alterado)``. A flag permite persistir uma migracao somente
    quando necessaria.
    """

    if dados_brutos is None:
        return banco_padrao(), True

    # ``historico`` e intencionalmente uma lista. Preservamo-lo antes da
    # conversao recursiva para que uma inicializacao normal nao pareca uma
    # migracao e nao provoque um PUT completo desnecessario a cada acesso.
    havia_lista_estrutural = _possui_lista_estrutural(dados_brutos)
    historico_original = (
        dados_brutos.get("historico") if isinstance(dados_brutos, dict) else None
    )
    convertido = converter_listas_para_dicionarios(dados_brutos)
    if not isinstance(convertido, dict):
        raise TypeError("A raiz do banco Firebase não é um objeto JSON válido.")
    if isinstance(dados_brutos, dict) and "historico" in dados_brutos:
        convertido["historico"] = historico_original

    banco: dict[str, Any] = {"obras": {}, "usuarios": {}, "historico": []}

    obras_brutas = convertido.get("obras", {})
    if not isinstance(obras_brutas, dict):
        obras_brutas = converter_listas_para_dicionarios(obras_brutas)
    if not isinstance(obras_brutas, dict):
        obras_brutas = {}

    for obra_nome, andares_brutos in obras_brutas.items():
        if not isinstance(andares_brutos, dict):
            andares_brutos = converter_listas_para_dicionarios(andares_brutos)
        if not isinstance(andares_brutos, dict):
            continue

        andares_ok: dict[str, Any] = {}
        for andar_nome, locais_brutos in andares_brutos.items():
            if not isinstance(locais_brutos, dict):
                locais_brutos = converter_listas_para_dicionarios(locais_brutos)
            if not isinstance(locais_brutos, dict):
                continue

            locais_ok: dict[str, Any] = {}
            for local_nome, atividades_brutas in locais_brutos.items():
                if not isinstance(atividades_brutas, dict):
                    atividades_brutas = converter_listas_para_dicionarios(
                        atividades_brutas
                    )
                if not isinstance(atividades_brutas, dict):
                    continue

                atividades_ok: dict[str, dict[str, str]] = {}
                for atividade_nome, dados_atividade in atividades_brutas.items():
                    atividades_ok[str(atividade_nome)] = _dados_atividade_seguros(
                        dados_atividade
                    )

                # Migracao de nomenclatura sem sobrescrever um registro novo.
                for nome_antigo in list(atividades_ok):
                    if nome_antigo.casefold() == "rejunte piso":
                        atividades_ok.setdefault(
                            "Piso acabado", atividades_ok[nome_antigo]
                        )
                        del atividades_ok[nome_antigo]

                locais_ok[str(local_nome)] = atividades_ok
            andares_ok[str(andar_nome)] = locais_ok
        banco["obras"][str(obra_nome)] = andares_ok

    usuarios_brutos = convertido.get("usuarios", {})
    if isinstance(usuarios_brutos, dict):
        for login, registro in usuarios_brutos.items():
            if not isinstance(registro, dict):
                continue
            perfil = str(registro.get("perfil", "visualizador"))
            if perfil not in PERFIS:
                perfil = "visualizador"
            usuario_ok = dict(registro)
            usuario_ok["perfil"] = perfil
            usuario_ok["nome"] = str(registro.get("nome", login))
            banco["usuarios"][str(login)] = usuario_ok

    if not banco["usuarios"]:
        banco["usuarios"]["admin"] = usuario_admin_padrao()

    historico_bruto = convertido.get("historico", {})
    if isinstance(historico_bruto, dict):
        itens = [
            historico_bruto[chave]
            for chave in sorted(historico_bruto, key=chave_ordenacao_natural)
        ]
    elif isinstance(historico_bruto, list):
        itens = historico_bruto
    else:
        itens = []

    banco["historico"] = [item for item in itens if isinstance(item, dict)][:300]

    # Comparacao JSON ignora identidade de objetos e detecta todas as correcoes.
    try:
        alterado = havia_lista_estrutural or (
            json.dumps(convertido, sort_keys=True, ensure_ascii=False)
            != json.dumps(banco, sort_keys=True, ensure_ascii=False)
        )
    except (TypeError, ValueError):
        alterado = True
    return banco, alterado


def nova_atividade() -> dict[str, str]:
    return {"status": "Não Iniciado", "obs": ""}


def criar_locais_padrao(andar: str) -> dict[str, Any]:
    prefixo = str(andar).strip()
    locais: dict[str, Any] = {}
    for numero in range(1, 15):
        nome = (
            f"{prefixo}{numero:02d}" if prefixo.isdigit() else f"{prefixo}-{numero:02d}"
        )
        locais[nome] = {servico: nova_atividade() for servico in SERVICOS_BASE}
    locais["Corredor"] = {servico: nova_atividade() for servico in SERVICOS_BASE}
    return locais


def eh_corredor(nome_local: str) -> bool:
    normalizado = (
        unicodedata.normalize("NFKD", str(nome_local))
        .encode("ascii", "ignore")
        .decode("ascii")
    )
    return "corredor" in normalizado.casefold()


def rotulo_tipo_local(nome_local: str) -> str:
    if eh_corredor(nome_local):
        return "Corredor"
    correspondencia = re.search(r"(\d{2})$", str(nome_local))
    if correspondencia:
        return f"Unidade {correspondencia.group(1)}"
    return str(nome_local)


# ---------------------------------------------------------------------------
# Repositorio Firebase REST
# ---------------------------------------------------------------------------


class FirebaseError(RuntimeError):
    pass


class FirebaseRepository:
    def __init__(
        self,
        url: str,
        auth_token: str = "",
        timeout: int = FIREBASE_TIMEOUT_SECONDS,
    ) -> None:
        self.url = self._com_auth(url, auth_token)
        self.timeout = timeout

    @staticmethod
    def _com_auth(url: str, token: str) -> str:
        if not token:
            return url
        separador = "&" if "?" in url else "?"
        return f"{url}{separador}auth={urllib.parse.quote(token, safe='')}"

    def _executar(
        self, metodo: str, payload: Any | None = None
    ) -> tuple[Any, dict[str, str]]:
        corpo = None
        if payload is not None:
            corpo = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        requisicao = urllib.request.Request(self.url, data=corpo, method=metodo)
        requisicao.add_header("Accept", "application/json")
        if corpo is not None:
            requisicao.add_header("Content-Type", "application/json; charset=utf-8")
        try:
            with urllib.request.urlopen(requisicao, timeout=self.timeout) as resposta:
                texto = resposta.read().decode("utf-8")
                dados = json.loads(texto) if texto else None
                return dados, dict(resposta.headers.items())
        except urllib.error.HTTPError as erro:
            detalhe = erro.read().decode("utf-8", errors="replace")
            raise FirebaseError(
                f"Firebase respondeu HTTP {erro.code}: {detalhe[:240]}"
            ) from erro
        except (urllib.error.URLError, TimeoutError, OSError) as erro:
            raise FirebaseError(f"Falha de conexão com o Firebase: {erro}") from erro
        except json.JSONDecodeError as erro:
            raise FirebaseError("O Firebase devolveu JSON inválido.") from erro

    def carregar(self) -> Any:
        dados, _ = self._executar("GET")
        return dados

    def substituir(self, banco: dict[str, Any]) -> None:
        self._executar("PUT", banco)

    def atualizar(self, alteracoes: dict[str, Any]) -> None:
        """PATCH atomico; valores ``None`` removem os caminhos indicados."""

        if alteracoes:
            self._executar("PATCH", alteracoes)


# ---------------------------------------------------------------------------
# Relatorio PDF matricial
# ---------------------------------------------------------------------------


def _texto_pdf(valor: Any) -> str:
    """Mantem acentos suportados pelas fontes basicas e troca glifos invalidos."""

    return str(valor or "").encode("latin-1", errors="replace").decode("latin-1")


def _linha_com_elipse(pdf: FPDF, texto: str, largura_maxima: float) -> str:
    sufixo = "..."
    candidato = texto.rstrip()
    while candidato and pdf.get_string_width(candidato + sufixo) > largura_maxima:
        candidato = candidato[:-1].rstrip()
    return (candidato + sufixo) if candidato else sufixo


def quebrar_observacao_pdf(
    pdf: FPDF,
    observacao: Any,
    largura_maxima: float,
    maximo_linhas: int = 3,
) -> list[str]:
    """Quebra por largura real da fonte, limita a 3 linhas e adiciona elipse."""

    texto = _texto_pdf(observacao)
    palavras = re.findall(r"\S+", texto)
    if not palavras:
        return []

    linhas: list[str] = []
    atual = ""
    indice = 0
    while indice < len(palavras):
        palavra = palavras[indice]
        candidato = palavra if not atual else f"{atual} {palavra}"
        if pdf.get_string_width(candidato) <= largura_maxima:
            atual = candidato
            indice += 1
            continue

        if atual:
            linhas.append(atual)
            atual = ""
        else:
            # Uma palavra maior que a celula e cortada sem entrar em loop.
            fragmento = ""
            for caractere in palavra:
                if pdf.get_string_width(fragmento + caractere) <= largura_maxima:
                    fragmento += caractere
                else:
                    break
            linhas.append(_linha_com_elipse(pdf, fragmento, largura_maxima))
            indice += 1

        if len(linhas) == maximo_linhas:
            if indice < len(palavras) or atual:
                linhas[-1] = _linha_com_elipse(pdf, linhas[-1], largura_maxima)
            return linhas

    if atual and len(linhas) < maximo_linhas:
        linhas.append(atual)

    if indice < len(palavras) and linhas:
        linhas[-1] = _linha_com_elipse(pdf, linhas[-1], largura_maxima)
    return linhas[:maximo_linhas]


def _cor_texto_contrastante(rgb: tuple[int, int, int]) -> tuple[int, int, int]:
    # Luminancia relativa simplificada: branco nos fundos escuros, preto nos claros.
    r, g, b = rgb
    luminancia = (0.299 * r) + (0.587 * g) + (0.114 * b)
    return (255, 255, 255) if luminancia < 155 else (0, 0, 0)


def desenhar_celula_status(
    pdf: FPDF,
    *,
    x: float,
    y: float,
    largura: float,
    altura: float,
    status: str,
    observacao: str,
) -> None:
    """Desenha fundo, observacao e borda em camadas deterministicas.

    Esta e a correcao do bug original. Nao usamos ``get_x()/get_y()`` depois
    de uma ``cell()`` que altera o cursor. O chamador fornece coordenadas
    absolutas. A ordem de pintura e:

      1. retangulo de fundo, sem borda;
      2. texto por coordenadas absolutas, centralizado horizontal/verticalmente;
      3. borda no perimetro, sem preenchimento.

    Assim nenhuma celula preenchida e desenhada por cima da observacao.
    """

    status_seguro = status if status in STATUS_PDF_RGB else "Não Iniciado"
    rgb = STATUS_PDF_RGB[status_seguro]

    pdf.set_fill_color(*rgb)
    pdf.rect(x, y, largura, altura, style="F")

    if status_seguro in {"Em Andamento", "Não Conforme"} and observacao.strip():
        pdf.set_font("helvetica", "B", 5.2)
        pdf.set_text_color(*_cor_texto_contrastante(rgb))
        margem_interna = 0.8
        linhas = quebrar_observacao_pdf(
            pdf,
            observacao,
            largura_maxima=largura - (2 * margem_interna),
            maximo_linhas=3,
        )

        altura_linha = 2.05
        altura_bloco = len(linhas) * altura_linha
        # ``text`` recebe a linha de base, por isso somamos 1,55 mm.
        primeira_base = y + ((altura - altura_bloco) / 2) + 1.55
        for indice, linha in enumerate(linhas):
            largura_texto = pdf.get_string_width(linha)
            x_texto = x + max(margem_interna, (largura - largura_texto) / 2)
            pdf.text(x_texto, primeira_base + (indice * altura_linha), linha)

    pdf.set_draw_color(70, 70, 70)
    pdf.set_line_width(0.18)
    pdf.rect(x, y, largura, altura, style="D")
    pdf.set_text_color(0, 0, 0)


def _local_da_coluna(andar: str, numero: int, locais: dict[str, Any]) -> str | None:
    esperado = f"{andar}{numero:02d}"
    if esperado in locais:
        return esperado
    esperado_hifen = f"{andar}-{numero:02d}"
    if esperado_hifen in locais:
        return esperado_hifen
    sufixo = f"{numero:02d}"
    candidatos = [
        nome for nome in locais if not eh_corredor(nome) and str(nome).endswith(sufixo)
    ]
    return min(candidatos, key=chave_ordenacao_natural) if candidatos else None


def gerar_pdf(
    banco_dados: dict[str, Any],
    obra: str,
    servico_escolhido: str,
    andares_ordenados: Sequence[str] | None = None,
    caminho_arquivo: str | os.PathLike[str] | None = None,
) -> bytes:
    """Gera o PDF matricial e retorna seus bytes.

    ``caminho_arquivo`` e opcional para manter compatibilidade com o fluxo
    desktop antigo. No PWA os bytes sao entregues diretamente ao FilePicker.
    """

    obras = banco_dados.get("obras", {})
    if obra not in obras or not isinstance(obras[obra], dict):
        raise ValueError("Obra não encontrada para geração do PDF.")

    andares = list(
        andares_ordenados or sorted(obras[obra], key=chave_ordenacao_natural)
    )
    pdf = FPDF(orientation="L", unit="mm", format="A4")
    pdf.set_margins(10, 8, 10)
    pdf.set_auto_page_break(auto=False)

    largura_andar = 20.0
    largura_apto = 16.0
    largura_corredor = 22.0
    altura_celula = 8.2
    largura_tabela = largura_andar + (14 * largura_apto) + largura_corredor
    margem_esquerda = (297.0 - largura_tabela) / 2

    def desenhar_titulo_e_legenda() -> float:
        pdf.set_font("helvetica", "B", 14)
        pdf.set_text_color(0, 0, 0)
        pdf.set_xy(10, 8)
        pdf.cell(
            277,
            7,
            _texto_pdf(f"RELATÓRIO DE VISTORIA - {obra.upper()}"),
            align="C",
            new_x=XPos.LMARGIN,
            new_y=YPos.NEXT,
        )
        pdf.set_font("helvetica", "B", 10)
        pdf.cell(
            277,
            5,
            _texto_pdf(f"Atividade: {servico_escolhido}"),
            align="C",
            new_x=XPos.LMARGIN,
            new_y=YPos.NEXT,
        )

        legenda = (
            ("Finalizado", "OK"),
            ("Não Conforme", "Pendência"),
            ("Em Andamento", "Andamento"),
            ("Existente", "Existente"),
            ("Não Iniciado", "Não iniciado"),
        )
        largura_item = 42
        x_legenda = (297 - (largura_item * len(legenda))) / 2
        y_legenda = 23
        pdf.set_font("helvetica", "", 7.5)
        for status_legenda, rotulo in legenda:
            rgb = STATUS_PDF_RGB[status_legenda]
            pdf.set_fill_color(*rgb)
            pdf.set_draw_color(70, 70, 70)
            pdf.rect(x_legenda, y_legenda, 6, 4, style="DF")
            pdf.set_text_color(0, 0, 0)
            pdf.text(x_legenda + 7.5, y_legenda + 3.1, _texto_pdf(rotulo))
            x_legenda += largura_item
        return 31.0

    def desenhar_cabecalho_tabela(y: float) -> float:
        pdf.set_fill_color(230, 230, 230)
        pdf.set_draw_color(70, 70, 70)
        pdf.set_line_width(0.18)
        pdf.rect(margem_esquerda, y, largura_andar, altura_celula, style="DF")
        pdf.set_font("helvetica", "B", 8)
        pdf.set_text_color(0, 0, 0)
        pdf.set_xy(margem_esquerda, y)
        pdf.cell(largura_andar, altura_celula, "Andar", align="C")
        x = margem_esquerda + largura_andar
        for numero in range(1, 15):
            pdf.rect(x, y, largura_apto, altura_celula, style="DF")
            pdf.set_xy(x, y)
            pdf.cell(largura_apto, altura_celula, f"{numero:02d}", align="C")
            x += largura_apto
        pdf.rect(x, y, largura_corredor, altura_celula, style="DF")
        pdf.set_xy(x, y)
        pdf.cell(largura_corredor, altura_celula, "Corr.", align="C")
        return y + altura_celula

    pdf.add_page()
    y_atual = desenhar_cabecalho_tabela(desenhar_titulo_e_legenda())

    for andar in andares:
        if y_atual + altura_celula > 199:
            pdf.add_page()
            y_atual = desenhar_cabecalho_tabela(desenhar_titulo_e_legenda())

        locais = obras[obra].get(str(andar), {})
        if not isinstance(locais, dict):
            locais = {}

        pdf.set_fill_color(245, 245, 245)
        pdf.set_draw_color(70, 70, 70)
        pdf.rect(
            margem_esquerda,
            y_atual,
            largura_andar,
            altura_celula,
            style="DF",
        )
        pdf.set_font("helvetica", "B", 8)
        pdf.set_text_color(0, 0, 0)
        pdf.set_xy(margem_esquerda, y_atual)
        pdf.cell(largura_andar, altura_celula, _texto_pdf(str(andar)), align="C")

        x_atual = margem_esquerda + largura_andar
        for numero in range(1, 15):
            local = _local_da_coluna(str(andar), numero, locais)
            dados_servico: dict[str, Any] = {}
            if local is not None:
                atividades = locais.get(local, {})
                if isinstance(atividades, dict):
                    candidato = atividades.get(servico_escolhido, {})
                    if isinstance(candidato, dict):
                        dados_servico = candidato
            desenhar_celula_status(
                pdf,
                x=x_atual,
                y=y_atual,
                largura=largura_apto,
                altura=altura_celula,
                status=str(dados_servico.get("status", "Não Iniciado")),
                observacao=_observacao_segura(dados_servico.get("obs", "")),
            )
            x_atual += largura_apto

        corredor = next((nome for nome in locais if eh_corredor(nome)), None)
        dados_corredor: dict[str, Any] = {}
        if corredor is not None:
            atividades = locais.get(corredor, {})
            if isinstance(atividades, dict):
                candidato = atividades.get(servico_escolhido, {})
                if isinstance(candidato, dict):
                    dados_corredor = candidato
        desenhar_celula_status(
            pdf,
            x=x_atual,
            y=y_atual,
            largura=largura_corredor,
            altura=altura_celula,
            status=str(dados_corredor.get("status", "Não Iniciado")),
            observacao=_observacao_segura(dados_corredor.get("obs", "")),
        )
        y_atual += altura_celula

    resultado = bytes(pdf.output())
    if caminho_arquivo is not None:
        Path(caminho_arquivo).write_bytes(resultado)
    return resultado


# ---------------------------------------------------------------------------
# Metricas
# ---------------------------------------------------------------------------


def calcular_metricas_obra(banco_dados: dict[str, Any], obra: str) -> dict[str, Any]:
    geral = Counter({status: 0 for status in STATUS})
    por_andar: dict[str, Counter[str]] = {}
    por_comodo: dict[str, Counter[str]] = {}
    por_atividade: dict[str, Counter[str]] = {}

    andares = banco_dados.get("obras", {}).get(obra, {})
    if not isinstance(andares, dict):
        andares = {}

    for andar, locais in andares.items():
        if not isinstance(locais, dict):
            continue
        contador_andar = por_andar.setdefault(str(andar), Counter())
        for local, atividades in locais.items():
            if not isinstance(atividades, dict):
                continue
            contador_comodo = por_comodo.setdefault(rotulo_tipo_local(local), Counter())
            for atividade, dados in atividades.items():
                dados_ok = _dados_atividade_seguros(dados)
                status = dados_ok["status"]
                geral[status] += 1
                contador_andar[status] += 1
                contador_comodo[status] += 1
                por_atividade.setdefault(str(atividade), Counter())[status] += 1

    return {
        "geral": geral,
        "por_andar": por_andar,
        "por_comodo": por_comodo,
        "por_atividade": por_atividade,
    }


def progresso_contador(contador: Counter[str]) -> tuple[int, int, float]:
    total = sum(contador.values())
    concluidas = contador["Finalizado"] + contador["Existente"]
    percentual = (concluidas / total) if total else 0.0
    return total, concluidas, percentual


# ---------------------------------------------------------------------------
# Aplicacao Flet
# ---------------------------------------------------------------------------


class AppVistoria:
    def __init__(self, page: ft.Page) -> None:
        self.page = page
        self.repo = FirebaseRepository(FIREBASE_URL, FIREBASE_AUTH_TOKEN)
        self.banco_dados: dict[str, Any] = banco_padrao()
        self.estado_sessao: dict[str, str | None] = {
            "usuario": None,
            "perfil": None,
            "nome": None,
        }
        self._lock_persistencia = asyncio.Lock()

        # Flet >= 0.80 substituiu page.client_storage por SharedPreferences.
        # O comportamento de "Manter-me logado" continua sendo local ao cliente.
        self.preferencias = ft.SharedPreferences()
        self.seletor_arquivo = ft.FilePicker()

        self.page.title = "App de Vistoria"
        self.page.theme_mode = ft.ThemeMode.LIGHT
        self.page.padding = 16
        self.page.spacing = 12
        self.page.bgcolor = ft.Colors.GREY_50
        self.page.window.width = 430
        self.page.window.height = 780

    # ------------------------------- ciclo de vida -------------------------

    async def iniciar(self) -> None:
        self._mostrar_carregando("Sincronizando dados...")
        try:
            dados_brutos = await asyncio.to_thread(self.repo.carregar)
            banco, alterado = normalizar_banco(dados_brutos)
            self.banco_dados = banco
            if alterado:
                await asyncio.to_thread(self.repo.substituir, banco)
        except (FirebaseError, TypeError, ValueError) as erro:
            self._mostrar_erro_inicial(str(erro))
            return

        usuario_salvo = await self.preferencias.get(SESSION_USER_KEY)
        if usuario_salvo and str(usuario_salvo) in self.banco_dados["usuarios"]:
            self._aplicar_sessao(str(usuario_salvo))
            self.abrir_tela_obras()
        else:
            if usuario_salvo:
                await self.preferencias.remove(SESSION_USER_KEY)
            self.abrir_tela_login()

    def _mostrar_carregando(self, mensagem: str) -> None:
        self.page.controls.clear()
        self.page.floating_action_button = None
        self.page.controls.append(
            ft.Column(
                controls=[
                    ft.ProgressRing(),
                    ft.Text(mensagem, color=ft.Colors.GREY_700),
                ],
                alignment=ft.MainAxisAlignment.CENTER,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                expand=True,
            )
        )
        self.page.update()

    def _mostrar_erro_inicial(self, mensagem: str) -> None:
        async def tentar_novamente(_: ft.Event[ft.Button]) -> None:
            await self.iniciar()

        self.page.controls.clear()
        self.page.floating_action_button = None
        self.page.controls.append(
            ft.Column(
                controls=[
                    ft.Icon(ft.Icons.CLOUD_OFF, size=54, color=ft.Colors.RED_500),
                    ft.Text(
                        "Não foi possível carregar o banco",
                        size=20,
                        weight=ft.FontWeight.BOLD,
                        text_align=ft.TextAlign.CENTER,
                    ),
                    ft.Text(
                        mensagem,
                        color=ft.Colors.GREY_700,
                        text_align=ft.TextAlign.CENTER,
                    ),
                    ft.FilledButton(
                        content="Tentar novamente",
                        icon=ft.Icons.REFRESH,
                        on_click=tentar_novamente,
                    ),
                ],
                alignment=ft.MainAxisAlignment.CENTER,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=16,
                expand=True,
            )
        )
        self.page.update()

    # ------------------------------- utilitarios UI ------------------------

    def _snack(
        self,
        mensagem: str,
        *,
        erro: bool = False,
        cor: ft.Colors | str | None = None,
    ) -> None:
        fundo = cor or (ft.Colors.RED_700 if erro else ft.Colors.GREEN_700)
        self.page.show_dialog(
            ft.SnackBar(
                content=ft.Text(mensagem, color=ft.Colors.WHITE),
                bgcolor=fundo,
                show_close_icon=True,
                close_icon_color=ft.Colors.WHITE,
            )
        )

    def _fechar_dialogo(self) -> None:
        self.page.pop_dialog()

    def _cabecalho(
        self,
        titulo: str,
        *,
        voltar: Callable[[], None] | None = None,
        acoes: Sequence[ft.Control] = (),
    ) -> ft.Row:
        controles: list[ft.Control] = []
        if voltar is not None:
            controles.append(
                ft.IconButton(
                    icon=ft.Icons.ARROW_BACK,
                    icon_color=ft.Colors.BLUE_700,
                    tooltip="Voltar",
                    on_click=lambda _: voltar(),
                )
            )
        else:
            controles.append(
                ft.Icon(ft.Icons.FACT_CHECK, color=ft.Colors.BLUE_700, size=30)
            )
        controles.append(
            ft.Text(
                titulo,
                size=20,
                weight=ft.FontWeight.BOLD,
                color=ft.Colors.BLUE_800,
                max_lines=2,
                overflow=ft.TextOverflow.ELLIPSIS,
                expand=True,
            )
        )
        controles.extend(acoes)
        return ft.Row(
            controls=controles,
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

    def _definir_tela(
        self,
        *,
        cabecalho: ft.Control,
        corpo: ft.Control,
        fab: ft.FloatingActionButton | None = None,
    ) -> None:
        self.page.controls.clear()
        self.page.floating_action_button = fab
        self.page.controls.append(
            ft.Column(
                controls=[cabecalho, ft.Divider(height=1), corpo],
                spacing=10,
                expand=True,
            )
        )
        self.page.update()

    def _cartao_navegacao(
        self,
        *,
        titulo: str,
        subtitulo: str,
        icone: ft.IconData,
        ao_clicar: Callable[[], None],
        cor: ft.Colors | str = ft.Colors.BLUE_700,
        menu: ft.Control | None = None,
    ) -> ft.Container:
        controles_finais: list[ft.Control] = []
        if menu is not None:
            controles_finais.append(menu)
        controles_finais.append(
            ft.Icon(ft.Icons.CHEVRON_RIGHT, color=ft.Colors.GREY_500)
        )
        return ft.Container(
            content=ft.Row(
                controls=[
                    ft.Container(
                        content=ft.Icon(icone, color=ft.Colors.WHITE),
                        bgcolor=cor,
                        width=42,
                        height=42,
                        alignment=ft.Alignment.CENTER,
                        border_radius=12,
                    ),
                    ft.Column(
                        controls=[
                            ft.Text(
                                titulo,
                                weight=ft.FontWeight.BOLD,
                                size=15,
                                max_lines=1,
                                overflow=ft.TextOverflow.ELLIPSIS,
                            ),
                            ft.Text(
                                subtitulo,
                                size=12,
                                color=ft.Colors.GREY_600,
                                max_lines=1,
                                overflow=ft.TextOverflow.ELLIPSIS,
                            ),
                        ],
                        spacing=2,
                        expand=True,
                    ),
                    *controles_finais,
                ],
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=12,
            bgcolor=ft.Colors.WHITE,
            border=ft.Border.all(1, ft.Colors.GREY_300),
            border_radius=12,
            ink=True,
            on_click=lambda _: ao_clicar(),
        )

    def _mostrar_formulario(
        self,
        *,
        titulo: str,
        conteudo: ft.Control,
        ao_confirmar: Callable[[], Any],
        confirmar_texto: str = "Salvar",
        confirmar_icone: ft.IconData = ft.Icons.SAVE,
        depois_de_salvar: Callable[[], None] | None = None,
    ) -> None:
        botao_salvar: ft.FilledButton

        async def confirmar(_: ft.Event[ft.FilledButton]) -> None:
            botao_salvar.disabled = True
            self.page.update(botao_salvar)
            try:
                resultado = ao_confirmar()
                if asyncio.iscoroutine(resultado):
                    resultado = await resultado
                if resultado is False:
                    botao_salvar.disabled = False
                    self.page.update(botao_salvar)
                    return
                self._fechar_dialogo()
                if depois_de_salvar is not None:
                    depois_de_salvar()
            except Exception as erro:  # noqa: BLE001 - fronteira de eventos da UI
                botao_salvar.disabled = False
                self.page.update(botao_salvar)
                self._snack(f"Erro: {erro}", erro=True)

        botao_salvar = ft.FilledButton(
            content=confirmar_texto,
            icon=confirmar_icone,
            on_click=confirmar,
        )
        dialogo = ft.AlertDialog(
            modal=True,
            title=ft.Text(titulo, weight=ft.FontWeight.BOLD),
            content=conteudo,
            actions=[
                ft.OutlinedButton(
                    content="Cancelar", on_click=lambda _: self._fechar_dialogo()
                ),
                botao_salvar,
            ],
            actions_alignment=ft.MainAxisAlignment.END,
            scrollable=True,
        )
        self.page.show_dialog(dialogo)

    def _confirmar_acao(
        self,
        *,
        titulo: str,
        mensagem: str,
        ao_confirmar: Callable[[], Any],
        depois: Callable[[], None] | None = None,
    ) -> None:
        botao: ft.FilledButton

        async def confirmar(_: ft.Event[ft.FilledButton]) -> None:
            botao.disabled = True
            self.page.update(botao)
            resultado = ao_confirmar()
            if asyncio.iscoroutine(resultado):
                resultado = await resultado
            if resultado is False:
                botao.disabled = False
                self.page.update(botao)
                return
            self._fechar_dialogo()
            if depois is not None:
                depois()

        botao = ft.FilledButton(
            content="Confirmar",
            icon=ft.Icons.DELETE_FOREVER,
            bgcolor=ft.Colors.RED_700,
            color=ft.Colors.WHITE,
            on_click=confirmar,
        )
        self.page.show_dialog(
            ft.AlertDialog(
                modal=True,
                title=ft.Text(titulo, weight=ft.FontWeight.BOLD),
                content=ft.Text(mensagem),
                actions=[
                    ft.OutlinedButton(
                        content="Cancelar", on_click=lambda _: self._fechar_dialogo()
                    ),
                    botao,
                ],
                actions_alignment=ft.MainAxisAlignment.END,
            )
        )

    # ------------------------------- sessao e persistencia -----------------

    def _aplicar_sessao(self, login: str) -> None:
        registro = self.banco_dados["usuarios"][login]
        self.estado_sessao.update(
            {
                "usuario": login,
                "perfil": registro.get("perfil", "visualizador"),
                "nome": registro.get("nome", login),
            }
        )

    @property
    def pode_editar(self) -> bool:
        return self.estado_sessao.get("perfil") in {"admin", "editor"}

    @property
    def eh_admin(self) -> bool:
        return self.estado_sessao.get("perfil") == "admin"

    def _registrar_historico(self, acao: str, detalhes: str) -> None:
        historico = self.banco_dados.setdefault("historico", [])
        if isinstance(historico, dict):
            historico = [
                historico[chave]
                for chave in sorted(historico, key=chave_ordenacao_natural)
            ]
            self.banco_dados["historico"] = historico
        if not isinstance(historico, list):
            historico = []
            self.banco_dados["historico"] = historico

        historico.insert(
            0,
            {
                "data": time.strftime("%d/%m/%Y %H:%M"),
                "user": self.estado_sessao.get("usuario") or "SISTEMA",
                "acao": acao,
                "detalhes": detalhes,
            },
        )
        del historico[300:]

    async def _alterar_e_persistir(
        self,
        *,
        acao: str,
        detalhes: str | Callable[[], str],
        mutacao: Callable[[], dict[str, Any]],
        mensagem_sucesso: str = "Sincronizado com o Firebase.",
    ) -> bool:
        """Executa mutacao, PATCH e rollback local se a rede falhar."""

        async with self._lock_persistencia:
            copia_anterior = copy.deepcopy(self.banco_dados)
            try:
                alteracoes = mutacao()
                if not alteracoes:
                    self._snack(
                        "Nenhum registro foi alterado.", cor=ft.Colors.ORANGE_700
                    )
                    return False
                texto_detalhes = detalhes() if callable(detalhes) else detalhes
                self._registrar_historico(acao, texto_detalhes)
                alteracoes["historico"] = self.banco_dados["historico"]
                await asyncio.to_thread(self.repo.atualizar, alteracoes)
            except Exception as erro:  # noqa: BLE001 - rollback de qualquer mutacao
                self.banco_dados = copia_anterior
                self._snack(f"Alteração desfeita: {erro}", erro=True)
                return False

        self._snack(mensagem_sucesso)
        return True

    async def _recarregar(self) -> bool:
        self._mostrar_carregando("Atualizando dados...")
        try:
            bruto = await asyncio.to_thread(self.repo.carregar)
            banco, alterado = normalizar_banco(bruto)
            self.banco_dados = banco
            if alterado:
                await asyncio.to_thread(self.repo.substituir, banco)
            usuario = self.estado_sessao.get("usuario")
            if usuario and usuario in banco["usuarios"]:
                self._aplicar_sessao(str(usuario))
            else:
                await self.preferencias.remove(SESSION_USER_KEY)
                self.estado_sessao = {"usuario": None, "perfil": None, "nome": None}
                self.abrir_tela_login()
                return False
            self._snack("Dados atualizados.")
            return True
        except (FirebaseError, TypeError, ValueError) as erro:
            self._snack(str(erro), erro=True)
            self.abrir_tela_obras()
            return False

    # ------------------------------- autenticacao -------------------------

    def abrir_tela_login(self) -> None:
        campo_usuario = ft.TextField(
            label="Usuário",
            prefix_icon=ft.Icons.PERSON,
            autofocus=True,
            capitalization=ft.TextCapitalization.NONE,
        )
        campo_senha = ft.TextField(
            label="Senha",
            prefix_icon=ft.Icons.LOCK,
            password=True,
            can_reveal_password=True,
        )
        manter_logado = ft.Checkbox(label="Manter-me logado", value=True)
        botao_entrar: ft.FilledButton

        async def entrar(_: ft.Event[ft.Control] | None = None) -> None:
            login = campo_usuario.value.strip().casefold()
            registro = self.banco_dados.get("usuarios", {}).get(login)
            if not isinstance(registro, dict) or not verificar_senha(
                registro, campo_senha.value
            ):
                campo_senha.value = ""
                campo_senha.error_text = "Usuário ou senha incorretos."
                self.page.update(campo_senha)
                return

            botao_entrar.disabled = True
            self.page.update(botao_entrar)
            self._aplicar_sessao(login)
            if manter_logado.value:
                await self.preferencias.set(SESSION_USER_KEY, login)
            else:
                await self.preferencias.remove(SESSION_USER_KEY)

            # Migra silenciosamente uma senha legada em texto puro. Se a rede
            # falhar, o login continua valido e a migracao sera tentada depois.
            if "senha" in registro and "senha_hash" not in registro:
                registro_novo = {
                    chave: valor
                    for chave, valor in registro.items()
                    if chave != "senha"
                }
                registro_novo.update(criar_hash_senha(campo_senha.value))
                try:
                    await asyncio.to_thread(
                        self.repo.atualizar,
                        {f"usuarios/{login}": registro_novo},
                    )
                    self.banco_dados["usuarios"][login] = registro_novo
                except FirebaseError:
                    pass

            self.abrir_tela_obras()

        async def enviar_por_enter(_: ft.Event[ft.TextField]) -> None:
            await entrar()

        campo_senha.on_submit = enviar_por_enter
        botao_entrar = ft.FilledButton(
            content="ENTRAR",
            icon=ft.Icons.LOGIN,
            width=250,
            on_click=entrar,
        )

        cartao = ft.Container(
            content=ft.Column(
                controls=[
                    ft.Icon(ft.Icons.APARTMENT, size=64, color=ft.Colors.BLUE_700),
                    ft.Text(
                        "App Vistoria Engenharia",
                        size=22,
                        weight=ft.FontWeight.BOLD,
                        color=ft.Colors.BLUE_800,
                        text_align=ft.TextAlign.CENTER,
                    ),
                    ft.Text(
                        "Auditoria e acompanhamento de serviços",
                        size=12,
                        color=ft.Colors.GREY_600,
                        text_align=ft.TextAlign.CENTER,
                    ),
                    campo_usuario,
                    campo_senha,
                    manter_logado,
                    botao_entrar,
                ],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=14,
            ),
            padding=28,
            border_radius=16,
            bgcolor=ft.Colors.WHITE,
            border=ft.Border.all(1, ft.Colors.GREY_300),
            width=350,
        )
        self._definir_tela(
            cabecalho=ft.Container(height=1),
            corpo=ft.Column(
                controls=[cartao],
                alignment=ft.MainAxisAlignment.CENTER,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                expand=True,
            ),
        )

    async def _logout(self) -> None:
        await self.preferencias.remove(SESSION_USER_KEY)
        self.estado_sessao = {"usuario": None, "perfil": None, "nome": None}
        self.abrir_tela_login()

    # ------------------------------- obras ---------------------------------

    def _resumo_obra(self, obra: str) -> str:
        andares = self.banco_dados["obras"].get(obra, {})
        quantidade_andares = len(andares) if isinstance(andares, dict) else 0
        metricas = calcular_metricas_obra(self.banco_dados, obra)
        total, concluidas, percentual = progresso_contador(metricas["geral"])
        return (
            f"{quantidade_andares} pavimento(s) • "
            f"{concluidas}/{total} concluídos ({percentual * 100:.0f}%)"
        )

    def abrir_tela_obras(self) -> None:
        async def atualizar(_: ft.Event[ft.IconButton]) -> None:
            if await self._recarregar():
                self.abrir_tela_obras()

        async def sair(_: ft.Event[ft.IconButton]) -> None:
            await self._logout()

        acoes: list[ft.Control] = [
            ft.IconButton(
                icon=ft.Icons.REFRESH,
                tooltip="Atualizar do Firebase",
                on_click=atualizar,
            )
        ]
        if self.estado_sessao.get("perfil") != "visualizador":
            acoes.append(
                ft.IconButton(
                    icon=ft.Icons.HISTORY,
                    tooltip="Histórico",
                    on_click=lambda _: self.abrir_tela_historico(),
                )
            )
        if self.eh_admin:
            acoes.append(
                ft.IconButton(
                    icon=ft.Icons.MANAGE_ACCOUNTS,
                    tooltip="Usuários",
                    on_click=lambda _: self.abrir_tela_usuarios(),
                )
            )
        acoes.append(
            ft.IconButton(
                icon=ft.Icons.LOGOUT,
                icon_color=ft.Colors.RED_600,
                tooltip="Sair",
                on_click=sair,
            )
        )

        cabecalho = ft.Column(
            controls=[
                ft.Text(
                    f"Olá, {self.estado_sessao.get('nome')}",
                    size=13,
                    color=ft.Colors.GREY_600,
                ),
                self._cabecalho("Obras", acoes=acoes),
            ],
            spacing=0,
        )

        lista = ft.ListView(expand=True, spacing=12, build_controls_on_demand=True)
        for obra in sorted(
            self.banco_dados.get("obras", {}), key=chave_ordenacao_natural
        ):
            menu: ft.Control | None = None
            if self.eh_admin:
                menu = ft.PopupMenuButton(
                    icon=ft.Icons.MORE_VERT,
                    tooltip="Opções da obra",
                    items=[
                        ft.PopupMenuItem(
                            content="Excluir obra",
                            icon=ft.Icons.DELETE,
                            on_click=lambda _, nome=obra: self._pedir_exclusao_obra(
                                nome
                            ),
                        )
                    ],
                )
            lista.controls.append(
                self._cartao_navegacao(
                    titulo=obra,
                    subtitulo=self._resumo_obra(obra),
                    icone=ft.Icons.DOMAIN,
                    cor=ft.Colors.BLUE_700,
                    ao_clicar=lambda nome=obra: self.abrir_tela_andares(nome),
                    menu=menu,
                )
            )

        if not lista.controls:
            lista.controls.append(
                ft.Container(
                    content=ft.Column(
                        controls=[
                            ft.Icon(
                                ft.Icons.HOME_WORK,
                                size=54,
                                color=ft.Colors.GREY_400,
                            ),
                            ft.Text(
                                "Nenhuma obra cadastrada.",
                                color=ft.Colors.GREY_600,
                                text_align=ft.TextAlign.CENTER,
                            ),
                        ],
                        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    padding=40,
                    alignment=ft.Alignment.CENTER,
                )
            )

        fab = None
        if self.eh_admin:
            fab = ft.FloatingActionButton(
                icon=ft.Icons.ADD,
                content="Nova obra",
                bgcolor=ft.Colors.BLUE_700,
                foreground_color=ft.Colors.WHITE,
                on_click=lambda _: self._formulario_nova_obra(),
            )
        self._definir_tela(cabecalho=cabecalho, corpo=lista, fab=fab)

    def _formulario_nova_obra(self) -> None:
        campo_nome = ft.TextField(label="Nome da obra", autofocus=True)
        campo_andares = ft.TextField(
            label="Quantidade inicial de pavimentos",
            value="0",
            keyboard_type=ft.KeyboardType.NUMBER,
        )
        criar_unidades = ft.Checkbox(
            label="Criar 14 apartamentos/locais e corredor em cada pavimento",
            value=True,
        )

        async def salvar() -> bool:
            try:
                nome = validar_chave_firebase(campo_nome.value, "Nome da obra")
                quantidade = int(campo_andares.value or "0")
                if quantidade < 0 or quantidade > 200:
                    raise ValueError("Informe entre 0 e 200 pavimentos.")
                if nome in self.banco_dados["obras"]:
                    raise ValueError("Já existe uma obra com esse nome.")
            except ValueError as erro:
                campo_nome.error_text = str(erro)
                self.page.update(campo_nome)
                return False

            def mutacao() -> dict[str, Any]:
                andares: dict[str, Any] = {}
                for numero in range(1, quantidade + 1):
                    andares[str(numero)] = (
                        criar_locais_padrao(str(numero)) if criar_unidades.value else {}
                    )
                self.banco_dados["obras"][nome] = andares
                return {f"obras/{nome}": andares}

            return await self._alterar_e_persistir(
                acao="Criação de obra",
                detalhes=f"Obra [{nome}] criada com {quantidade} pavimento(s).",
                mutacao=mutacao,
                mensagem_sucesso="Obra criada.",
            )

        self._mostrar_formulario(
            titulo="Nova obra",
            conteudo=ft.Column(
                controls=[campo_nome, campo_andares, criar_unidades],
                tight=True,
                width=360,
            ),
            ao_confirmar=salvar,
            depois_de_salvar=self.abrir_tela_obras,
        )

    def _pedir_exclusao_obra(self, obra: str) -> None:
        async def excluir() -> bool:
            def mutacao() -> dict[str, Any]:
                self.banco_dados["obras"].pop(obra, None)
                return {f"obras/{obra}": None}

            return await self._alterar_e_persistir(
                acao="Exclusão de obra",
                detalhes=f"Obra [{obra}] excluída.",
                mutacao=mutacao,
                mensagem_sucesso="Obra excluída.",
            )

        self._confirmar_acao(
            titulo="Excluir obra",
            mensagem=(
                f"Excluir definitivamente a obra “{obra}”, com todos os pavimentos, "
                "locais, atividades e observações?"
            ),
            ao_confirmar=excluir,
            depois=self.abrir_tela_obras,
        )

    # ------------------------------- pavimentos ----------------------------

    def _cor_progresso_local(self, atividades: dict[str, Any]) -> ft.Colors | str:
        if not atividades:
            return ft.Colors.GREY_400
        estados = [
            _dados_atividade_seguros(dados)["status"] for dados in atividades.values()
        ]
        if "Não Conforme" in estados:
            return ft.Colors.RED_500
        if all(status in {"Finalizado", "Existente"} for status in estados):
            if all(status == "Existente" for status in estados):
                return ft.Colors.ORANGE_500
            return ft.Colors.GREEN_500
        if any(status != "Não Iniciado" for status in estados):
            return ft.Colors.BLUE_500
        return ft.Colors.GREY_400

    def abrir_tela_andares(self, obra: str) -> None:
        if obra not in self.banco_dados.get("obras", {}):
            self._snack("Obra não encontrada.", erro=True)
            self.abrir_tela_obras()
            return

        acoes = [
            ft.IconButton(
                icon=ft.Icons.BAR_CHART,
                tooltip="Métricas",
                on_click=lambda _: self.abrir_tela_dashboard(obra),
            ),
            ft.IconButton(
                icon=ft.Icons.NOTES,
                tooltip="Galeria de observações",
                on_click=lambda _: self.abrir_tela_observacoes(obra),
            ),
            ft.IconButton(
                icon=ft.Icons.PICTURE_AS_PDF,
                icon_color=ft.Colors.RED_700,
                tooltip="Relatório",
                on_click=lambda _: self._escolher_relatorio(obra),
            ),
        ]
        cabecalho = self._cabecalho(
            obra,
            voltar=self.abrir_tela_obras,
            acoes=acoes,
        )
        lista = ft.ListView(expand=True, spacing=10, build_controls_on_demand=True)

        andares = self.banco_dados["obras"][obra]
        for andar in sorted(andares, key=chave_ordenacao_natural):
            locais = andares.get(andar, {})
            total_locais = len(locais) if isinstance(locais, dict) else 0
            menu = None
            if self.eh_admin:
                menu = ft.PopupMenuButton(
                    icon=ft.Icons.MORE_VERT,
                    items=[
                        ft.PopupMenuItem(
                            content="Excluir pavimento",
                            icon=ft.Icons.DELETE,
                            on_click=lambda _, nome=andar: self._pedir_exclusao_andar(
                                obra, nome
                            ),
                        )
                    ],
                )
            lista.controls.append(
                self._cartao_navegacao(
                    titulo=f"{andar}º Pavimento"
                    if str(andar).isdigit()
                    else str(andar),
                    subtitulo=f"{total_locais} apartamento(s)/local(is)",
                    icone=ft.Icons.LAYERS,
                    cor=ft.Colors.INDIGO_600,
                    ao_clicar=lambda nome=andar: self.abrir_tela_apartamentos(
                        obra, nome
                    ),
                    menu=menu,
                )
            )

        if not lista.controls:
            lista.controls.append(
                ft.Container(
                    content=ft.Text(
                        "Nenhum pavimento cadastrado.",
                        color=ft.Colors.GREY_600,
                        text_align=ft.TextAlign.CENTER,
                    ),
                    padding=40,
                    alignment=ft.Alignment.CENTER,
                )
            )

        fab = None
        if self.pode_editar:
            fab = ft.FloatingActionButton(
                icon=ft.Icons.APPS,
                tooltip="Ferramentas em lote",
                bgcolor=ft.Colors.BLUE_700,
                foreground_color=ft.Colors.WHITE,
                on_click=lambda _: self._abrir_menu_ferramentas(obra),
            )

        rodape: list[ft.Control] = [lista]
        if self.pode_editar:
            rodape.append(
                ft.OutlinedButton(
                    content="Adicionar pavimento",
                    icon=ft.Icons.ADD,
                    on_click=lambda _: self._formulario_novo_andar(obra),
                )
            )
        corpo = ft.Column(controls=rodape, expand=True)
        self._definir_tela(cabecalho=cabecalho, corpo=corpo, fab=fab)

    def _formulario_novo_andar(self, obra: str) -> None:
        campo = ft.TextField(label="Nome/número do pavimento", autofocus=True)
        criar_unidades = ft.Checkbox(
            label="Criar 14 apartamentos/locais e corredor com tarefas-base",
            value=True,
        )

        async def salvar() -> bool:
            try:
                andar = validar_chave_firebase(campo.value, "Pavimento")
                if andar in self.banco_dados["obras"][obra]:
                    raise ValueError("Este pavimento já existe.")
            except ValueError as erro:
                campo.error_text = str(erro)
                self.page.update(campo)
                return False

            def mutacao() -> dict[str, Any]:
                locais = criar_locais_padrao(andar) if criar_unidades.value else {}
                self.banco_dados["obras"][obra][andar] = locais
                return {f"obras/{obra}/{andar}": locais}

            return await self._alterar_e_persistir(
                acao="Criação de pavimento",
                detalhes=f"[{obra}] Pavimento [{andar}] criado.",
                mutacao=mutacao,
                mensagem_sucesso="Pavimento criado.",
            )

        self._mostrar_formulario(
            titulo="Novo pavimento",
            conteudo=ft.Column(controls=[campo, criar_unidades], tight=True, width=360),
            ao_confirmar=salvar,
            depois_de_salvar=lambda: self.abrir_tela_andares(obra),
        )

    def _pedir_exclusao_andar(self, obra: str, andar: str) -> None:
        async def excluir() -> bool:
            def mutacao() -> dict[str, Any]:
                self.banco_dados["obras"][obra].pop(andar, None)
                return {f"obras/{obra}/{andar}": None}

            return await self._alterar_e_persistir(
                acao="Exclusão de pavimento",
                detalhes=f"[{obra}] Pavimento [{andar}] excluído.",
                mutacao=mutacao,
                mensagem_sucesso="Pavimento excluído.",
            )

        self._confirmar_acao(
            titulo="Excluir pavimento",
            mensagem=f"Excluir o pavimento “{andar}” e todos os seus dados?",
            ao_confirmar=excluir,
            depois=lambda: self.abrir_tela_andares(obra),
        )

    # ------------------------------- apartamentos/locais -------------------

    def abrir_tela_apartamentos(self, obra: str, andar: str) -> None:
        try:
            locais = self.banco_dados["obras"][obra][andar]
        except KeyError:
            self._snack("Pavimento não encontrado.", erro=True)
            self.abrir_tela_andares(obra)
            return

        titulo = f"{andar}º Pavimento" if str(andar).isdigit() else str(andar)
        cabecalho = self._cabecalho(
            titulo,
            voltar=lambda: self.abrir_tela_andares(obra),
        )

        grade = ft.GridView(
            expand=True,
            max_extent=125,
            child_aspect_ratio=1.0,
            spacing=12,
            run_spacing=12,
            build_controls_on_demand=True,
        )
        for local in sorted(locais, key=chave_ordenacao_natural):
            atividades = locais.get(local, {})
            cor = self._cor_progresso_local(atividades)
            menu = None
            if self.eh_admin:
                menu = ft.PopupMenuButton(
                    icon=ft.Icons.MORE_VERT,
                    icon_color=ft.Colors.WHITE,
                    items=[
                        ft.PopupMenuItem(
                            content="Excluir local",
                            icon=ft.Icons.DELETE,
                            on_click=lambda _, nome=local: self._pedir_exclusao_local(
                                obra, andar, nome
                            ),
                        )
                    ],
                )
            grade.controls.append(
                ft.Container(
                    content=ft.Column(
                        controls=[
                            ft.Row(
                                controls=[menu] if menu else [],
                                alignment=ft.MainAxisAlignment.END,
                                height=28,
                            ),
                            ft.Icon(
                                ft.Icons.ROOFING
                                if eh_corredor(local)
                                else ft.Icons.HOME,
                                color=ft.Colors.WHITE,
                                size=28,
                            ),
                            ft.Text(
                                local,
                                size=15 if eh_corredor(local) else 20,
                                weight=ft.FontWeight.BOLD,
                                color=ft.Colors.WHITE,
                                text_align=ft.TextAlign.CENTER,
                                max_lines=2,
                                overflow=ft.TextOverflow.ELLIPSIS,
                            ),
                        ],
                        spacing=2,
                        alignment=ft.MainAxisAlignment.CENTER,
                        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    bgcolor=cor,
                    border_radius=12,
                    padding=6,
                    ink=True,
                    on_click=lambda _, nome=local: self.abrir_tela_atividades(
                        obra, andar, nome
                    ),
                )
            )

        corpo_controles: list[ft.Control] = [grade]
        if self.pode_editar:
            corpo_controles.append(
                ft.OutlinedButton(
                    content="Adicionar apartamento/local",
                    icon=ft.Icons.ADD_HOME,
                    on_click=lambda _: self._formulario_novo_local(obra, andar),
                )
            )
        self._definir_tela(
            cabecalho=cabecalho,
            corpo=ft.Column(controls=corpo_controles, expand=True),
        )

    def _formulario_novo_local(self, obra: str, andar: str) -> None:
        campo = ft.TextField(label="Nome do apartamento/local", autofocus=True)
        incluir_base = ft.Checkbox(label="Incluir tarefas-base", value=True)

        async def salvar() -> bool:
            try:
                local = validar_chave_firebase(campo.value, "Local")
                if local in self.banco_dados["obras"][obra][andar]:
                    raise ValueError("Este local já existe no pavimento.")
            except ValueError as erro:
                campo.error_text = str(erro)
                self.page.update(campo)
                return False

            def mutacao() -> dict[str, Any]:
                atividades = (
                    {servico: nova_atividade() for servico in SERVICOS_BASE}
                    if incluir_base.value
                    else {}
                )
                self.banco_dados["obras"][obra][andar][local] = atividades
                return {f"obras/{obra}/{andar}/{local}": atividades}

            return await self._alterar_e_persistir(
                acao="Criação de local",
                detalhes=f"[{obra}/{andar}] Local [{local}] criado.",
                mutacao=mutacao,
                mensagem_sucesso="Local criado.",
            )

        self._mostrar_formulario(
            titulo="Novo apartamento/local",
            conteudo=ft.Column(controls=[campo, incluir_base], tight=True, width=360),
            ao_confirmar=salvar,
            depois_de_salvar=lambda: self.abrir_tela_apartamentos(obra, andar),
        )

    def _pedir_exclusao_local(self, obra: str, andar: str, local: str) -> None:
        async def excluir() -> bool:
            def mutacao() -> dict[str, Any]:
                self.banco_dados["obras"][obra][andar].pop(local, None)
                return {f"obras/{obra}/{andar}/{local}": None}

            return await self._alterar_e_persistir(
                acao="Exclusão de local",
                detalhes=f"[{obra}/{andar}] Local [{local}] excluído.",
                mutacao=mutacao,
                mensagem_sucesso="Local excluído.",
            )

        self._confirmar_acao(
            titulo="Excluir apartamento/local",
            mensagem=f"Excluir “{local}” e todas as suas atividades?",
            ao_confirmar=excluir,
            depois=lambda: self.abrir_tela_apartamentos(obra, andar),
        )

    # ------------------------------- atividades ----------------------------

    def abrir_tela_atividades(self, obra: str, andar: str, local: str) -> None:
        try:
            atividades = self.banco_dados["obras"][obra][andar][local]
        except KeyError:
            self._snack("Local não encontrado.", erro=True)
            self.abrir_tela_apartamentos(obra, andar)
            return

        titulo = local if eh_corredor(local) else f"Apto/Local {local}"
        cabecalho = self._cabecalho(
            titulo,
            voltar=lambda: self.abrir_tela_apartamentos(obra, andar),
        )
        lista = ft.ListView(expand=True, spacing=10, build_controls_on_demand=True)

        for atividade in sorted(atividades, key=chave_ordenacao_natural):
            dados = _dados_atividade_seguros(atividades[atividade])
            cor = STATUS_FLET_COLOR[dados["status"]]
            botoes: list[ft.Control] = []
            if self.pode_editar:
                botoes.append(
                    ft.IconButton(
                        icon=ft.Icons.EDIT,
                        icon_color=cor,
                        tooltip="Editar",
                        on_click=lambda _, nome=atividade: self._editar_atividade(
                            obra, andar, local, nome
                        ),
                    )
                )
            if self.eh_admin:
                botoes.append(
                    ft.IconButton(
                        icon=ft.Icons.DELETE_OUTLINE,
                        icon_color=ft.Colors.RED_600,
                        tooltip="Excluir",
                        on_click=lambda _, nome=atividade: (
                            self._pedir_exclusao_atividade(obra, andar, local, nome)
                        ),
                    )
                )

            lista.controls.append(
                ft.Container(
                    content=ft.Row(
                        controls=[
                            ft.Container(
                                width=12,
                                height=56,
                                bgcolor=cor,
                                border_radius=6,
                            ),
                            ft.Column(
                                controls=[
                                    ft.Text(
                                        atividade,
                                        weight=ft.FontWeight.BOLD,
                                        max_lines=2,
                                        overflow=ft.TextOverflow.ELLIPSIS,
                                    ),
                                    ft.Text(
                                        dados["status"],
                                        size=12,
                                        weight=ft.FontWeight.BOLD,
                                        color=cor,
                                    ),
                                    ft.Text(
                                        dados["obs"] or "Sem observação",
                                        size=12,
                                        color=ft.Colors.GREY_600,
                                        max_lines=2,
                                        overflow=ft.TextOverflow.ELLIPSIS,
                                    ),
                                ],
                                spacing=2,
                                expand=True,
                            ),
                            *botoes,
                        ],
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    bgcolor=ft.Colors.WHITE,
                    padding=10,
                    border=ft.Border.all(1, ft.Colors.GREY_300),
                    border_radius=10,
                    on_click=(
                        (
                            lambda _, nome=atividade: self._editar_atividade(
                                obra, andar, local, nome
                            )
                        )
                        if self.pode_editar
                        else None
                    ),
                )
            )

        corpo_controles: list[ft.Control] = [lista]
        if self.pode_editar:
            corpo_controles.append(
                ft.FilledButton(
                    content="Nova atividade",
                    icon=ft.Icons.ADD_TASK,
                    on_click=lambda _: self._formulario_nova_atividade(
                        obra, andar, local
                    ),
                )
            )
        self._definir_tela(
            cabecalho=cabecalho,
            corpo=ft.Column(controls=corpo_controles, expand=True),
        )

    def _editar_atividade(
        self, obra: str, andar: str, local: str, atividade: str
    ) -> None:
        if not self.pode_editar:
            self._snack("Seu perfil é somente leitura.", erro=True)
            return
        dados_atuais = _dados_atividade_seguros(
            self.banco_dados["obras"][obra][andar][local][atividade]
        )
        campo_status = ft.Dropdown(
            label="Status",
            value=dados_atuais["status"],
            options=[ft.DropdownOption(key=status, text=status) for status in STATUS],
        )
        campo_obs = ft.TextField(
            label="Observação",
            value=dados_atuais["obs"],
            multiline=True,
            min_lines=2,
            max_lines=5,
            max_length=800,
        )
        limpar_ao_finalizar = ft.Checkbox(
            label="Limpar observação ao marcar como Finalizado",
            value=True,
        )

        async def salvar() -> bool:
            status = campo_status.value or "Não Iniciado"
            observacao = campo_obs.value.strip()
            if status == "Finalizado" and limpar_ao_finalizar.value:
                observacao = ""
            novos_dados = {"status": status, "obs": observacao}

            def mutacao() -> dict[str, Any]:
                self.banco_dados["obras"][obra][andar][local][atividade] = novos_dados
                return {f"obras/{obra}/{andar}/{local}/{atividade}": novos_dados}

            return await self._alterar_e_persistir(
                acao="Atualização de atividade",
                detalhes=(
                    f"[{obra}/{andar}/{local}] [{atividade}] alterada para [{status}]."
                ),
                mutacao=mutacao,
                mensagem_sucesso="Atividade atualizada.",
            )

        self._mostrar_formulario(
            titulo=atividade,
            conteudo=ft.Column(
                controls=[campo_status, campo_obs, limpar_ao_finalizar],
                tight=True,
                width=380,
            ),
            ao_confirmar=salvar,
            depois_de_salvar=lambda: self.abrir_tela_atividades(obra, andar, local),
        )

    def _formulario_nova_atividade(self, obra: str, andar: str, local: str) -> None:
        campo_nome = ft.TextField(label="Nome da atividade", autofocus=True)

        async def salvar() -> bool:
            try:
                atividade = validar_chave_firebase(campo_nome.value, "Atividade")
                if atividade in self.banco_dados["obras"][obra][andar][local]:
                    raise ValueError("Esta atividade já existe no local.")
            except ValueError as erro:
                campo_nome.error_text = str(erro)
                self.page.update(campo_nome)
                return False

            dados = nova_atividade()

            def mutacao() -> dict[str, Any]:
                self.banco_dados["obras"][obra][andar][local][atividade] = dados
                return {f"obras/{obra}/{andar}/{local}/{atividade}": dados}

            return await self._alterar_e_persistir(
                acao="Criação de atividade",
                detalhes=f"[{obra}/{andar}/{local}] Atividade [{atividade}] criada.",
                mutacao=mutacao,
                mensagem_sucesso="Atividade criada.",
            )

        self._mostrar_formulario(
            titulo="Nova atividade",
            conteudo=campo_nome,
            ao_confirmar=salvar,
            depois_de_salvar=lambda: self.abrir_tela_atividades(obra, andar, local),
        )

    def _pedir_exclusao_atividade(
        self, obra: str, andar: str, local: str, atividade: str
    ) -> None:
        async def excluir() -> bool:
            def mutacao() -> dict[str, Any]:
                self.banco_dados["obras"][obra][andar][local].pop(atividade, None)
                return {f"obras/{obra}/{andar}/{local}/{atividade}": None}

            return await self._alterar_e_persistir(
                acao="Exclusão de atividade",
                detalhes=f"[{obra}/{andar}/{local}] Atividade [{atividade}] excluída.",
                mutacao=mutacao,
                mensagem_sucesso="Atividade excluída.",
            )

        self._confirmar_acao(
            titulo="Excluir atividade",
            mensagem=f"Excluir a atividade “{atividade}” deste local?",
            ao_confirmar=excluir,
            depois=lambda: self.abrir_tela_atividades(obra, andar, local),
        )

    # ------------------------------- historico -----------------------------

    def abrir_tela_historico(self) -> None:
        if self.estado_sessao.get("perfil") == "visualizador":
            self._snack("Seu perfil não pode acessar o histórico.", erro=True)
            return
        cabecalho = self._cabecalho("Histórico", voltar=self.abrir_tela_obras)
        lista = ft.ListView(expand=True, spacing=8, build_controls_on_demand=True)
        historico = self.banco_dados.get("historico", [])
        if not isinstance(historico, list):
            historico = []
        for item in historico:
            if not isinstance(item, dict):
                continue
            lista.controls.append(
                ft.Container(
                    content=ft.Column(
                        controls=[
                            ft.Row(
                                controls=[
                                    ft.Text(
                                        f"{item.get('data', '')} • {item.get('user', '')}",
                                        size=11,
                                        weight=ft.FontWeight.BOLD,
                                        expand=True,
                                    ),
                                    ft.Text(
                                        str(item.get("acao", "")),
                                        size=11,
                                        weight=ft.FontWeight.BOLD,
                                        color=ft.Colors.BLUE_700,
                                    ),
                                ],
                                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                            ),
                            ft.Text(str(item.get("detalhes", "")), size=13),
                        ],
                        spacing=4,
                    ),
                    bgcolor=ft.Colors.WHITE,
                    padding=12,
                    border=ft.Border.all(1, ft.Colors.GREY_300),
                    border_radius=8,
                )
            )
        if not lista.controls:
            lista.controls.append(ft.Text("Histórico vazio.", color=ft.Colors.GREY_600))
        self._definir_tela(cabecalho=cabecalho, corpo=lista)

    # ------------------------------- usuarios ------------------------------

    def abrir_tela_usuarios(self) -> None:
        if not self.eh_admin:
            self._snack("Acesso restrito ao administrador.", erro=True)
            return
        cabecalho = self._cabecalho("Usuários", voltar=self.abrir_tela_obras)
        lista = ft.ListView(expand=True, spacing=10, build_controls_on_demand=True)
        for login in sorted(self.banco_dados["usuarios"], key=chave_ordenacao_natural):
            registro = self.banco_dados["usuarios"][login]
            lista.controls.append(
                ft.Container(
                    content=ft.Row(
                        controls=[
                            ft.Icon(
                                ft.Icons.ADMIN_PANEL_SETTINGS
                                if registro.get("perfil") == "admin"
                                else ft.Icons.PERSON,
                                color=ft.Colors.BLUE_700,
                            ),
                            ft.Column(
                                controls=[
                                    ft.Text(
                                        str(registro.get("nome", login)),
                                        weight=ft.FontWeight.BOLD,
                                    ),
                                    ft.Text(
                                        f"{login} • {PERFIS.get(registro.get('perfil'), 'Visualizador')}",
                                        size=12,
                                        color=ft.Colors.GREY_600,
                                    ),
                                ],
                                expand=True,
                                spacing=2,
                            ),
                            ft.IconButton(
                                icon=ft.Icons.EDIT,
                                tooltip="Editar usuário",
                                on_click=lambda _, usuario=login: (
                                    self._formulario_usuario(usuario)
                                ),
                            ),
                            ft.IconButton(
                                icon=ft.Icons.DELETE_OUTLINE,
                                icon_color=ft.Colors.RED_600,
                                tooltip="Excluir usuário",
                                disabled=login == self.estado_sessao.get("usuario"),
                                on_click=lambda _, usuario=login: (
                                    self._pedir_exclusao_usuario(usuario)
                                ),
                            ),
                        ],
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    bgcolor=ft.Colors.WHITE,
                    padding=10,
                    border=ft.Border.all(1, ft.Colors.GREY_300),
                    border_radius=10,
                )
            )
        fab = ft.FloatingActionButton(
            icon=ft.Icons.PERSON_ADD,
            bgcolor=ft.Colors.BLUE_700,
            foreground_color=ft.Colors.WHITE,
            tooltip="Novo usuário",
            on_click=lambda _: self._formulario_usuario(None),
        )
        self._definir_tela(cabecalho=cabecalho, corpo=lista, fab=fab)

    def _formulario_usuario(self, login_existente: str | None) -> None:
        registro = (
            self.banco_dados["usuarios"].get(login_existente, {})
            if login_existente
            else {}
        )
        campo_login = ft.TextField(
            label="Login",
            value=login_existente or "",
            read_only=login_existente is not None,
            autofocus=login_existente is None,
        )
        campo_nome = ft.TextField(
            label="Nome",
            value=str(registro.get("nome", "")),
        )
        campo_senha = ft.TextField(
            label="Nova senha" if login_existente else "Senha",
            password=True,
            can_reveal_password=True,
            hint_text="Deixe vazia para manter" if login_existente else None,
        )
        campo_perfil = ft.Dropdown(
            label="Perfil",
            value=str(registro.get("perfil", "editor")),
            options=[
                ft.DropdownOption(key=chave, text=rotulo)
                for chave, rotulo in PERFIS.items()
            ],
        )

        async def salvar() -> bool:
            try:
                login = validar_chave_firebase(
                    campo_login.value.strip().casefold(), "Login"
                )
                nome = campo_nome.value.strip() or login
                if not login_existente and login in self.banco_dados["usuarios"]:
                    raise ValueError("Este login já existe.")
                if not login_existente and len(campo_senha.value) < 4:
                    raise ValueError("A senha deve ter pelo menos 4 caracteres.")
                if campo_senha.value and len(campo_senha.value) < 4:
                    raise ValueError("A senha deve ter pelo menos 4 caracteres.")
                perfil = campo_perfil.value or "visualizador"
                if perfil not in PERFIS:
                    raise ValueError("Perfil inválido.")
            except ValueError as erro:
                campo_login.error_text = str(erro)
                self.page.update(campo_login)
                return False

            atual = dict(registro)
            atual["nome"] = nome
            atual["perfil"] = perfil
            if campo_senha.value:
                atual.pop("senha", None)
                atual.pop("senha_hash", None)
                atual.pop("senha_salt", None)
                atual.pop("senha_iteracoes", None)
                atual.update(criar_hash_senha(campo_senha.value))

            def mutacao() -> dict[str, Any]:
                self.banco_dados["usuarios"][login] = atual
                return {f"usuarios/{login}": atual}

            return await self._alterar_e_persistir(
                acao="Gestão de usuário",
                detalhes=f"Usuário [{login}] gravado com perfil [{perfil}].",
                mutacao=mutacao,
                mensagem_sucesso="Usuário salvo.",
            )

        self._mostrar_formulario(
            titulo="Editar usuário" if login_existente else "Novo usuário",
            conteudo=ft.Column(
                controls=[campo_login, campo_nome, campo_senha, campo_perfil],
                tight=True,
                width=360,
            ),
            ao_confirmar=salvar,
            depois_de_salvar=self.abrir_tela_usuarios,
        )

    def _pedir_exclusao_usuario(self, login: str) -> None:
        if login == self.estado_sessao.get("usuario"):
            self._snack("Não é possível excluir o usuário conectado.", erro=True)
            return
        admins = [
            nome
            for nome, registro in self.banco_dados["usuarios"].items()
            if registro.get("perfil") == "admin"
        ]
        if login in admins and len(admins) == 1:
            self._snack("Não é possível excluir o último administrador.", erro=True)
            return

        async def excluir() -> bool:
            def mutacao() -> dict[str, Any]:
                self.banco_dados["usuarios"].pop(login, None)
                return {f"usuarios/{login}": None}

            return await self._alterar_e_persistir(
                acao="Exclusão de usuário",
                detalhes=f"Usuário [{login}] excluído.",
                mutacao=mutacao,
                mensagem_sucesso="Usuário excluído.",
            )

        self._confirmar_acao(
            titulo="Excluir usuário",
            mensagem=f"Excluir o usuário “{login}”?",
            ao_confirmar=excluir,
            depois=self.abrir_tela_usuarios,
        )

    # ------------------------------- ferramentas em lote -------------------

    def _todos_servicos(self, obra: str) -> list[str]:
        servicos = set(SERVICOS_BASE)
        for locais in self.banco_dados["obras"].get(obra, {}).values():
            if not isinstance(locais, dict):
                continue
            for atividades in locais.values():
                if isinstance(atividades, dict):
                    servicos.update(str(nome) for nome in atividades)
        return sorted(servicos, key=chave_ordenacao_natural)

    def _abrir_menu_ferramentas(self, obra: str) -> None:
        def abrir(tela: Callable[[str], None]) -> None:
            self._fechar_dialogo()
            tela(obra)

        itens = (
            (
                "Status rápido",
                ft.Icons.CHECKLIST,
                ft.Colors.ORANGE_700,
                self._formulario_status_lote,
            ),
            (
                "Nova tarefa",
                ft.Icons.PLAYLIST_ADD,
                ft.Colors.PURPLE_700,
                self._formulario_distribuir_tarefa,
            ),
            (
                "Remover tarefa",
                ft.Icons.PLAYLIST_REMOVE,
                ft.Colors.RED_800,
                self._formulario_remover_tarefa,
            ),
            (
                "Observação",
                ft.Icons.NOTE_ADD,
                ft.Colors.DEEP_PURPLE_600,
                self._formulario_observacao_lote,
            ),
        )
        grade = ft.GridView(
            controls=[
                ft.Container(
                    content=ft.Column(
                        controls=[
                            ft.Icon(icone, size=34, color=ft.Colors.WHITE),
                            ft.Text(
                                rotulo,
                                color=ft.Colors.WHITE,
                                size=11,
                                weight=ft.FontWeight.BOLD,
                                text_align=ft.TextAlign.CENTER,
                            ),
                        ],
                        alignment=ft.MainAxisAlignment.CENTER,
                        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    bgcolor=cor,
                    border_radius=12,
                    ink=True,
                    on_click=lambda _, destino=tela: abrir(destino),
                )
                for rotulo, icone, cor, tela in itens
            ],
            max_extent=120,
            child_aspect_ratio=1.0,
            spacing=12,
            run_spacing=12,
            height=270,
        )
        self.page.show_dialog(
            ft.AlertDialog(
                title=ft.Text("Ferramentas em lote", weight=ft.FontWeight.BOLD),
                content=ft.Container(content=grade, width=340),
                actions=[
                    ft.OutlinedButton(
                        content="Fechar", on_click=lambda _: self._fechar_dialogo()
                    )
                ],
            )
        )

    def _controles_escopo_lote(
        self,
        obra: str,
        *,
        primeiro_andar_selecionado: bool = False,
    ) -> tuple[ft.Control, Callable[[], tuple[list[str], str]]]:
        andares = sorted(self.banco_dados["obras"][obra], key=chave_ordenacao_natural)
        checks: dict[str, ft.Checkbox] = {}
        grade_andares = ft.GridView(
            max_extent=145,
            child_aspect_ratio=3.6,
            spacing=2,
            run_spacing=2,
            height=min(160, max(55, ((len(andares) + 1) // 2) * 48)),
        )
        for indice, andar in enumerate(andares):
            check = ft.Checkbox(
                label=f"{andar}º" if str(andar).isdigit() else str(andar),
                value=primeiro_andar_selecionado and indice == 0,
            )
            checks[andar] = check
            grade_andares.controls.append(check)

        def alternar_todos(_: ft.Event[ft.OutlinedButton]) -> None:
            novo_valor = not checks or not all(check.value for check in checks.values())
            for check in checks.values():
                check.value = novo_valor
            self.page.update(grade_andares)

        nomes_locais: set[str] = set()
        for locais in self.banco_dados["obras"][obra].values():
            if isinstance(locais, dict):
                nomes_locais.update(str(nome) for nome in locais)

        opcoes_filtro = [
            ft.DropdownOption(key="__todos__", text="Todos os locais"),
            ft.DropdownOption(
                key="__apartamentos__", text="Somente apartamentos/locais"
            ),
            ft.DropdownOption(key="__corredor__", text="Somente corredor"),
        ]
        opcoes_filtro.extend(
            ft.DropdownOption(
                key=f"__unidade_{numero:02d}__", text=f"Unidade {numero:02d}"
            )
            for numero in range(1, 15)
        )
        opcoes_filtro.extend(
            ft.DropdownOption(key=f"__local__{nome}", text=f"Nome exato: {nome}")
            for nome in sorted(nomes_locais, key=chave_ordenacao_natural)
        )
        filtro_local = ft.Dropdown(
            label="Filtro de apartamento/local",
            value="__todos__",
            options=opcoes_filtro,
            enable_search=True,
        )

        conteudo = ft.Column(
            controls=[
                ft.Row(
                    controls=[
                        ft.Text("Pavimentos", weight=ft.FontWeight.BOLD, expand=True),
                        ft.OutlinedButton(
                            content="Marcar/desmarcar todos",
                            icon=ft.Icons.SELECT_ALL,
                            on_click=alternar_todos,
                        ),
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),
                grade_andares,
                filtro_local,
            ],
            tight=True,
        )

        def obter() -> tuple[list[str], str]:
            selecionados = [andar for andar, check in checks.items() if check.value]
            return selecionados, filtro_local.value or "__todos__"

        return conteudo, obter

    def _iterar_alvos_lote(
        self,
        obra: str,
        andares: Iterable[str],
        filtro_local: str,
    ) -> Iterable[tuple[str, str, dict[str, Any]]]:
        for andar in andares:
            locais = self.banco_dados["obras"][obra].get(andar, {})
            if not isinstance(locais, dict):
                continue
            for local, atividades in locais.items():
                if not isinstance(atividades, dict):
                    continue
                incluir = False
                if filtro_local == "__todos__":
                    incluir = True
                elif filtro_local == "__apartamentos__":
                    incluir = not eh_corredor(local)
                elif filtro_local == "__corredor__":
                    incluir = eh_corredor(local)
                elif filtro_local.startswith("__unidade_"):
                    sufixo = filtro_local.removeprefix("__unidade_").removesuffix("__")
                    incluir = not eh_corredor(local) and str(local).endswith(sufixo)
                elif filtro_local.startswith("__local__"):
                    incluir = str(local) == filtro_local.removeprefix("__local__")
                if incluir:
                    yield andar, str(local), atividades

    def _formulario_status_lote(self, obra: str) -> None:
        servicos = self._todos_servicos(obra)
        campo_tarefa = ft.Dropdown(
            label="Atividade",
            options=[ft.DropdownOption(key=nome, text=nome) for nome in servicos],
            enable_search=True,
        )
        campo_status = ft.Dropdown(
            label="Novo status",
            value="Finalizado",
            options=[ft.DropdownOption(key=status, text=status) for status in STATUS],
        )
        campo_obs = ft.TextField(
            label="Observação em lote (opcional)",
            multiline=True,
            min_lines=2,
            max_lines=4,
            max_length=800,
            hint_text="Se vazia, a observação atual será preservada.",
        )
        criar_ausente = ft.Checkbox(
            label="Criar a atividade nos locais onde ela não existe",
            value=False,
        )
        limpar_finalizado = ft.Checkbox(
            label="Limpar observação ao marcar como Finalizado",
            value=True,
        )
        escopo, obter_escopo = self._controles_escopo_lote(
            obra, primeiro_andar_selecionado=True
        )
        quantidade = 0

        async def aplicar() -> bool:
            nonlocal quantidade
            tarefa = campo_tarefa.value
            andares, filtro = obter_escopo()
            if not tarefa:
                campo_tarefa.error_text = "Escolha uma atividade."
                self.page.update(campo_tarefa)
                return False
            if not andares:
                self._snack("Selecione pelo menos um pavimento.", erro=True)
                return False
            status = campo_status.value or "Não Iniciado"
            observacao_digitada = campo_obs.value.strip()

            def mutacao() -> dict[str, Any]:
                nonlocal quantidade
                quantidade = 0
                alteracoes: dict[str, Any] = {}
                for andar, local, atividades in self._iterar_alvos_lote(
                    obra, andares, filtro
                ):
                    existe = tarefa in atividades
                    if not existe and not criar_ausente.value:
                        continue
                    dados = _dados_atividade_seguros(
                        atividades.get(tarefa, nova_atividade())
                    )
                    dados["status"] = status
                    if observacao_digitada:
                        dados["obs"] = observacao_digitada
                    elif status == "Finalizado" and limpar_finalizado.value:
                        dados["obs"] = ""
                    atividades[tarefa] = dados
                    alteracoes[f"obras/{obra}/{andar}/{local}/{tarefa}"] = dados
                    quantidade += 1
                return alteracoes

            return await self._alterar_e_persistir(
                acao="Status em lote",
                detalhes=lambda: (
                    f"[{obra}] Status [{status}] aplicado à atividade [{tarefa}] "
                    f"em {quantidade} local(is)."
                ),
                mutacao=mutacao,
                mensagem_sucesso="Status em lote aplicado.",
            )

        self._mostrar_formulario(
            titulo="Status rápido",
            conteudo=ft.Column(
                controls=[
                    campo_tarefa,
                    campo_status,
                    campo_obs,
                    criar_ausente,
                    limpar_finalizado,
                    ft.Divider(),
                    escopo,
                ],
                tight=True,
                width=410,
            ),
            ao_confirmar=aplicar,
            confirmar_texto="Aplicar",
            confirmar_icone=ft.Icons.DONE_ALL,
            depois_de_salvar=lambda: self.abrir_tela_andares(obra),
        )

    def _formulario_distribuir_tarefa(self, obra: str) -> None:
        campo_nome = ft.TextField(label="Nome da nova atividade", autofocus=True)
        campo_status = ft.Dropdown(
            label="Status inicial",
            value="Não Iniciado",
            options=[ft.DropdownOption(key=status, text=status) for status in STATUS],
        )
        campo_obs = ft.TextField(
            label="Observação inicial (opcional)",
            multiline=True,
            min_lines=2,
            max_lines=4,
            max_length=800,
        )
        sobrescrever = ft.Checkbox(
            label="Sobrescrever a atividade se ela já existir",
            value=False,
        )
        escopo, obter_escopo = self._controles_escopo_lote(obra)
        quantidade = 0

        async def aplicar() -> bool:
            nonlocal quantidade
            try:
                tarefa = validar_chave_firebase(campo_nome.value, "Atividade")
            except ValueError as erro:
                campo_nome.error_text = str(erro)
                self.page.update(campo_nome)
                return False
            andares, filtro = obter_escopo()
            if not andares:
                self._snack("Selecione pelo menos um pavimento.", erro=True)
                return False
            dados_novos = {
                "status": campo_status.value or "Não Iniciado",
                "obs": campo_obs.value.strip(),
            }

            def mutacao() -> dict[str, Any]:
                nonlocal quantidade
                quantidade = 0
                alteracoes: dict[str, Any] = {}
                for andar, local, atividades in self._iterar_alvos_lote(
                    obra, andares, filtro
                ):
                    if tarefa in atividades and not sobrescrever.value:
                        continue
                    dados = dict(dados_novos)
                    atividades[tarefa] = dados
                    alteracoes[f"obras/{obra}/{andar}/{local}/{tarefa}"] = dados
                    quantidade += 1
                return alteracoes

            return await self._alterar_e_persistir(
                acao="Distribuição de tarefa",
                detalhes=lambda: (
                    f"[{obra}] Atividade [{tarefa}] distribuída a {quantidade} local(is)."
                ),
                mutacao=mutacao,
                mensagem_sucesso="Tarefa distribuída.",
            )

        self._mostrar_formulario(
            titulo="Distribuir nova tarefa",
            conteudo=ft.Column(
                controls=[
                    campo_nome,
                    campo_status,
                    campo_obs,
                    sobrescrever,
                    ft.Divider(),
                    escopo,
                ],
                tight=True,
                width=410,
            ),
            ao_confirmar=aplicar,
            confirmar_texto="Distribuir",
            confirmar_icone=ft.Icons.PLAYLIST_ADD,
            depois_de_salvar=lambda: self.abrir_tela_andares(obra),
        )

    def _formulario_remover_tarefa(self, obra: str) -> None:
        campo_tarefa = ft.Dropdown(
            label="Atividade a remover",
            options=[
                ft.DropdownOption(key=nome, text=nome)
                for nome in self._todos_servicos(obra)
            ],
            enable_search=True,
        )
        escopo, obter_escopo = self._controles_escopo_lote(obra)
        quantidade = 0

        async def aplicar() -> bool:
            nonlocal quantidade
            tarefa = campo_tarefa.value
            if not tarefa:
                campo_tarefa.error_text = "Escolha uma atividade."
                self.page.update(campo_tarefa)
                return False
            andares, filtro = obter_escopo()
            if not andares:
                self._snack("Selecione pelo menos um pavimento.", erro=True)
                return False

            def mutacao() -> dict[str, Any]:
                nonlocal quantidade
                quantidade = 0
                alteracoes: dict[str, Any] = {}
                for andar, local, atividades in self._iterar_alvos_lote(
                    obra, andares, filtro
                ):
                    if tarefa not in atividades:
                        continue
                    del atividades[tarefa]
                    alteracoes[f"obras/{obra}/{andar}/{local}/{tarefa}"] = None
                    quantidade += 1
                return alteracoes

            return await self._alterar_e_persistir(
                acao="Remoção de tarefa em lote",
                detalhes=lambda: (
                    f"[{obra}] Atividade [{tarefa}] removida de {quantidade} local(is)."
                ),
                mutacao=mutacao,
                mensagem_sucesso="Tarefa removida dos locais selecionados.",
            )

        self._mostrar_formulario(
            titulo="Remover tarefa em lote",
            conteudo=ft.Column(
                controls=[
                    campo_tarefa,
                    ft.Text(
                        "A remoção apaga também o status e a observação da atividade nos alvos.",
                        size=12,
                        color=ft.Colors.RED_700,
                    ),
                    ft.Divider(),
                    escopo,
                ],
                tight=True,
                width=410,
            ),
            ao_confirmar=aplicar,
            confirmar_texto="Remover",
            confirmar_icone=ft.Icons.DELETE_SWEEP,
            depois_de_salvar=lambda: self.abrir_tela_andares(obra),
        )

    def _formulario_observacao_lote(self, obra: str) -> None:
        campo_tarefa = ft.Dropdown(
            label="Atividade",
            options=[
                ft.DropdownOption(key=nome, text=nome)
                for nome in self._todos_servicos(obra)
            ],
            enable_search=True,
        )
        campo_obs = ft.TextField(
            label="Observação",
            multiline=True,
            min_lines=2,
            max_lines=5,
            max_length=800,
        )
        modo = ft.Dropdown(
            label="Modo",
            value="substituir",
            options=[
                ft.DropdownOption(key="substituir", text="Substituir observação atual"),
                ft.DropdownOption(key="acrescentar", text="Acrescentar ao texto atual"),
            ],
        )
        escopo, obter_escopo = self._controles_escopo_lote(
            obra, primeiro_andar_selecionado=True
        )
        quantidade = 0

        async def aplicar() -> bool:
            nonlocal quantidade
            tarefa = campo_tarefa.value
            observacao = campo_obs.value.strip()
            if not tarefa or not observacao:
                self._snack("Escolha a atividade e informe a observação.", erro=True)
                return False
            andares, filtro = obter_escopo()
            if not andares:
                self._snack("Selecione pelo menos um pavimento.", erro=True)
                return False

            def mutacao() -> dict[str, Any]:
                nonlocal quantidade
                quantidade = 0
                alteracoes: dict[str, Any] = {}
                for andar, local, atividades in self._iterar_alvos_lote(
                    obra, andares, filtro
                ):
                    if tarefa not in atividades:
                        continue
                    dados = _dados_atividade_seguros(atividades[tarefa])
                    if modo.value == "acrescentar" and dados["obs"].strip():
                        dados["obs"] = f"{dados['obs'].rstrip()} | {observacao}"
                    else:
                        dados["obs"] = observacao
                    atividades[tarefa] = dados
                    alteracoes[f"obras/{obra}/{andar}/{local}/{tarefa}"] = dados
                    quantidade += 1
                return alteracoes

            return await self._alterar_e_persistir(
                acao="Observação em lote",
                detalhes=lambda: (
                    f"[{obra}] Observação aplicada à atividade [{tarefa}] "
                    f"em {quantidade} local(is)."
                ),
                mutacao=mutacao,
                mensagem_sucesso="Observações gravadas.",
            )

        self._mostrar_formulario(
            titulo="Inserir observação em lote",
            conteudo=ft.Column(
                controls=[
                    campo_tarefa,
                    campo_obs,
                    modo,
                    ft.Divider(),
                    escopo,
                ],
                tight=True,
                width=410,
            ),
            ao_confirmar=aplicar,
            confirmar_texto="Gravar",
            confirmar_icone=ft.Icons.NOTE_ADD,
            depois_de_salvar=lambda: self.abrir_tela_andares(obra),
        )

    # ------------------------------- galeria de observacoes ----------------

    def abrir_tela_observacoes(self, obra: str) -> None:
        cabecalho = self._cabecalho(
            "Galeria de Observações",
            voltar=lambda: self.abrir_tela_andares(obra),
        )
        lista = ft.ListView(expand=True, spacing=12, build_controls_on_demand=True)

        andares = self.banco_dados["obras"].get(obra, {})
        for andar in sorted(andares, key=chave_ordenacao_natural):
            locais = andares.get(andar, {})
            if not isinstance(locais, dict):
                continue
            for local in sorted(locais, key=chave_ordenacao_natural):
                atividades = locais.get(local, {})
                if not isinstance(atividades, dict):
                    continue
                for atividade in sorted(atividades, key=chave_ordenacao_natural):
                    dados = _dados_atividade_seguros(atividades[atividade])
                    if not dados["obs"].strip():
                        continue
                    cor = STATUS_FLET_COLOR[dados["status"]]
                    lista.controls.append(
                        ft.Container(
                            content=ft.Column(
                                controls=[
                                    ft.Row(
                                        controls=[
                                            ft.Text(
                                                f"{andar}º • {local}",
                                                weight=ft.FontWeight.BOLD,
                                                size=15,
                                                expand=True,
                                            ),
                                            ft.Text(
                                                dados["status"],
                                                color=cor,
                                                weight=ft.FontWeight.BOLD,
                                                size=12,
                                            ),
                                        ],
                                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                                    ),
                                    ft.Text(
                                        atividade,
                                        color=ft.Colors.GREY_700,
                                        size=13,
                                        weight=ft.FontWeight.BOLD,
                                    ),
                                    ft.Divider(height=4),
                                    ft.Text(dados["obs"], size=14, selectable=True),
                                ],
                                spacing=5,
                            ),
                            bgcolor=ft.Colors.WHITE,
                            padding=14,
                            border_radius=10,
                            border=ft.Border.all(2, cor),
                        )
                    )

        if not lista.controls:
            lista.controls.append(
                ft.Container(
                    content=ft.Column(
                        controls=[
                            ft.Icon(
                                ft.Icons.SPEAKER_NOTES_OFF,
                                size=48,
                                color=ft.Colors.GREY_400,
                            ),
                            ft.Text(
                                "Nenhuma observação encontrada nesta obra.",
                                color=ft.Colors.GREY_600,
                                text_align=ft.TextAlign.CENTER,
                            ),
                        ],
                        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    padding=40,
                    alignment=ft.Alignment.CENTER,
                )
            )
        self._definir_tela(cabecalho=cabecalho, corpo=lista)

    # ------------------------------- dashboard -----------------------------

    @staticmethod
    def _item_progresso(
        titulo: str,
        contador: Counter[str],
        *,
        cor: ft.Colors | str,
    ) -> ft.Column:
        total, concluidas, percentual = progresso_contador(contador)
        return ft.Column(
            controls=[
                ft.Row(
                    controls=[
                        ft.Text(
                            titulo,
                            size=13,
                            weight=ft.FontWeight.BOLD,
                            color=ft.Colors.GREY_800,
                            expand=True,
                            max_lines=2,
                            overflow=ft.TextOverflow.ELLIPSIS,
                        ),
                        ft.Text(
                            f"{concluidas}/{total} ({percentual * 100:.0f}%)",
                            size=12,
                            weight=ft.FontWeight.BOLD,
                            color=ft.Colors.GREY_700,
                        ),
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),
                ft.ProgressBar(
                    value=percentual,
                    color=cor,
                    bgcolor=ft.Colors.GREY_200,
                    bar_height=7,
                    border_radius=4,
                ),
            ],
            spacing=4,
        )

    @staticmethod
    def _cartao_contador(
        rotulo: str,
        valor: int,
        cor: ft.Colors | str,
        fundo: ft.Colors | str,
    ) -> ft.Container:
        return ft.Container(
            content=ft.Column(
                controls=[
                    ft.Text(rotulo, size=10, weight=ft.FontWeight.BOLD, color=cor),
                    ft.Text(str(valor), size=20, weight=ft.FontWeight.BOLD, color=cor),
                ],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=2,
            ),
            bgcolor=fundo,
            padding=10,
            border_radius=8,
            col={"xs": 6, "sm": 3},
        )

    def _contadores_locais_andar(
        self, obra: str, andar: str
    ) -> dict[str, Counter[str]]:
        resultado: dict[str, Counter[str]] = {}
        locais = self.banco_dados["obras"][obra].get(andar, {})
        if not isinstance(locais, dict):
            return resultado
        for local, atividades in locais.items():
            contador: Counter[str] = Counter()
            if isinstance(atividades, dict):
                for dados in atividades.values():
                    contador[_dados_atividade_seguros(dados)["status"]] += 1
            resultado[str(local)] = contador
        return resultado

    def abrir_tela_dashboard(self, obra: str) -> None:
        cabecalho = self._cabecalho(
            "Métricas da Obra",
            voltar=lambda: self.abrir_tela_andares(obra),
        )
        metricas = calcular_metricas_obra(self.banco_dados, obra)
        painel = ft.Column(expand=True, scroll=ft.ScrollMode.AUTO, spacing=14)
        botoes: dict[str, ft.Button] = {}

        def conteudo_geral() -> list[ft.Control]:
            geral: Counter[str] = metricas["geral"]
            total, concluidas, percentual = progresso_contador(geral)
            progresso = ft.Container(
                content=ft.Column(
                    controls=[
                        ft.Text(
                            "PROGRESSO TOTAL CONCLUÍDO",
                            size=11,
                            weight=ft.FontWeight.BOLD,
                            color=ft.Colors.GREY_600,
                        ),
                        ft.Row(
                            controls=[
                                ft.Text(
                                    f"{percentual * 100:.1f}%",
                                    size=34,
                                    weight=ft.FontWeight.BOLD,
                                    color=ft.Colors.BLUE_800,
                                ),
                                ft.Text(
                                    f"{concluidas} de {total}",
                                    size=13,
                                    color=ft.Colors.GREY_600,
                                ),
                            ],
                            vertical_alignment=ft.CrossAxisAlignment.END,
                        ),
                        ft.ProgressBar(
                            value=percentual,
                            color=ft.Colors.BLUE_700,
                            bgcolor=ft.Colors.GREY_200,
                            bar_height=10,
                            border_radius=5,
                        ),
                    ],
                    spacing=5,
                ),
                bgcolor=ft.Colors.BLUE_50,
                padding=16,
                border_radius=10,
            )
            contadores = ft.ResponsiveRow(
                controls=[
                    self._cartao_contador(
                        "OK",
                        geral["Finalizado"],
                        ft.Colors.GREEN_700,
                        ft.Colors.GREEN_50,
                    ),
                    self._cartao_contador(
                        "ANDAM.",
                        geral["Em Andamento"],
                        ft.Colors.BLUE_700,
                        ft.Colors.BLUE_50,
                    ),
                    self._cartao_contador(
                        "PEND.",
                        geral["Não Conforme"],
                        ft.Colors.RED_700,
                        ft.Colors.RED_50,
                    ),
                    self._cartao_contador(
                        "EXIST.",
                        geral["Existente"],
                        ft.Colors.ORANGE_700,
                        ft.Colors.ORANGE_50,
                    ),
                ],
                spacing=6,
                run_spacing=6,
            )
            itens: list[ft.Control] = [
                progresso,
                contadores,
                ft.Text(
                    "EVOLUÇÃO POR ATIVIDADE",
                    size=12,
                    weight=ft.FontWeight.BOLD,
                    color=ft.Colors.GREY_600,
                ),
            ]
            for atividade in sorted(
                metricas["por_atividade"], key=chave_ordenacao_natural
            ):
                itens.append(
                    self._item_progresso(
                        atividade,
                        metricas["por_atividade"][atividade],
                        cor=ft.Colors.GREEN_500,
                    )
                )
            return itens

        def conteudo_andar() -> list[ft.Control]:
            itens: list[ft.Control] = [
                ft.Text(
                    "PROGRESSO POR PAVIMENTO",
                    size=12,
                    weight=ft.FontWeight.BOLD,
                    color=ft.Colors.GREY_600,
                )
            ]
            for andar in sorted(metricas["por_andar"], key=chave_ordenacao_natural):
                rotulo = f"{andar}º Pavimento" if str(andar).isdigit() else str(andar)
                itens.append(
                    self._item_progresso(
                        rotulo,
                        metricas["por_andar"][andar],
                        cor=ft.Colors.BLUE_600,
                    )
                )
            if len(itens) == 1:
                itens.append(ft.Text("Não há pavimentos cadastrados."))
            return itens

        def conteudo_comodo() -> list[ft.Control]:
            andares = sorted(
                self.banco_dados["obras"][obra], key=chave_ordenacao_natural
            )
            lista_locais = ft.Column(spacing=14)
            seletor = ft.Dropdown(
                label="Pavimento",
                value=andares[0] if andares else None,
                options=[
                    ft.DropdownOption(key=andar, text=str(andar)) for andar in andares
                ],
            )

            def preencher(andar: str | None, *, atualizar: bool = False) -> None:
                lista_locais.controls.clear()
                if andar is None:
                    lista_locais.controls.append(
                        ft.Text("Não há pavimentos cadastrados.")
                    )
                else:
                    contadores = self._contadores_locais_andar(obra, andar)
                    for local in sorted(contadores, key=chave_ordenacao_natural):
                        lista_locais.controls.append(
                            self._item_progresso(
                                local,
                                contadores[local],
                                cor=ft.Colors.ORANGE_500,
                            )
                        )
                if atualizar:
                    self.page.update(lista_locais)

            def selecionar(_: ft.Event[ft.Dropdown]) -> None:
                preencher(seletor.value, atualizar=True)

            seletor.on_select = selecionar
            preencher(seletor.value)
            return [
                seletor,
                ft.Text(
                    "EVOLUÇÃO DOS APARTAMENTOS/LOCAIS",
                    size=12,
                    weight=ft.FontWeight.BOLD,
                    color=ft.Colors.GREY_600,
                ),
                lista_locais,
            ]

        geradores = {
            "geral": conteudo_geral,
            "andar": conteudo_andar,
            "comodo": conteudo_comodo,
        }

        def mostrar(aba: str, *, atualizar: bool = True) -> None:
            painel.controls = geradores[aba]()
            for chave, botao in botoes.items():
                selecionado = chave == aba
                botao.bgcolor = (
                    ft.Colors.BLUE_700 if selecionado else ft.Colors.GREY_200
                )
                botao.color = ft.Colors.WHITE if selecionado else ft.Colors.BLACK87
            if atualizar:
                self.page.update()

        botoes["geral"] = ft.Button(
            content="Visão Geral",
            expand=True,
            on_click=lambda _: mostrar("geral"),
        )
        botoes["andar"] = ft.Button(
            content="Por Andar",
            expand=True,
            on_click=lambda _: mostrar("andar"),
        )
        botoes["comodo"] = ft.Button(
            content="Por Cômodo",
            expand=True,
            on_click=lambda _: mostrar("comodo"),
        )
        navegacao = ft.Row(
            controls=[botoes["geral"], botoes["andar"], botoes["comodo"]],
            spacing=5,
        )
        mostrar("geral", atualizar=False)
        self._definir_tela(
            cabecalho=cabecalho,
            corpo=ft.Column(controls=[navegacao, painel], expand=True),
        )

    # ------------------------------- relatorio -----------------------------

    def _escolher_relatorio(self, obra: str) -> None:
        servicos = self._todos_servicos(obra)
        if not servicos:
            self._snack("A obra não possui atividades para relatar.", erro=True)
            return
        campo = ft.Dropdown(
            label="Atividade",
            options=[ft.DropdownOption(key=nome, text=nome) for nome in servicos],
            enable_search=True,
            value=servicos[0],
        )

        def abrir() -> bool:
            if not campo.value:
                campo.error_text = "Escolha uma atividade."
                self.page.update(campo)
                return False
            atividade = campo.value
            # A tela e aberta pelo callback posterior para evitar dois dialogs.
            self._atividade_relatorio_pendente = atividade
            return True

        self._mostrar_formulario(
            titulo="Relatório matricial",
            conteudo=campo,
            ao_confirmar=abrir,
            confirmar_texto="Visualizar",
            confirmar_icone=ft.Icons.GRID_ON,
            depois_de_salvar=lambda: self.abrir_tela_relatorio(
                obra, self._atividade_relatorio_pendente
            ),
        )

    @staticmethod
    def _slug_arquivo(texto: str) -> str:
        base = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
        base = re.sub(r"[^A-Za-z0-9_-]+", "_", base).strip("_")
        return base or "relatorio"

    async def _exportar_pdf(
        self,
        obra: str,
        atividade: str,
        botao: ft.FilledButton,
    ) -> None:
        botao.disabled = True
        botao.content = "Gerando PDF..."
        self.page.update(botao)
        try:
            andares = sorted(
                self.banco_dados["obras"][obra], key=chave_ordenacao_natural
            )
            # Copia consistente: o worker nao le o dicionario enquanto a UI o altera.
            fotografia = copy.deepcopy(self.banco_dados)
            conteudo = await asyncio.to_thread(
                gerar_pdf,
                fotografia,
                obra,
                atividade,
                andares,
            )
            nome = (
                f"Relatorio_{self._slug_arquivo(obra)}_"
                f"{self._slug_arquivo(atividade)}.pdf"
            )
            await self.seletor_arquivo.save_file(
                dialog_title="Salvar relatório de vistoria",
                file_name=nome,
                file_type=ft.FilePickerFileType.CUSTOM,
                allowed_extensions=["pdf"],
                src_bytes=conteudo,
            )
            self._snack("PDF gerado com sucesso.")
        except Exception as erro:  # noqa: BLE001 - fronteira de exportacao
            self._snack(f"Não foi possível gerar o PDF: {erro}", erro=True)
        finally:
            botao.disabled = False
            botao.content = "Gerar PDF (A4 paisagem)"
            try:
                self.page.update(botao)
            except RuntimeError:
                # O usuario pode sair da tela enquanto o arquivo e preparado.
                pass

    def abrir_tela_relatorio(self, obra: str, atividade: str) -> None:
        cabecalho = self._cabecalho(
            f"Relatório: {atividade}",
            voltar=lambda: self.abrir_tela_andares(obra),
        )
        botao_pdf: ft.FilledButton

        async def exportar(_: ft.Event[ft.FilledButton]) -> None:
            await self._exportar_pdf(obra, atividade, botao_pdf)

        botao_pdf = ft.FilledButton(
            content="Gerar PDF (A4 paisagem)",
            icon=ft.Icons.PICTURE_AS_PDF,
            bgcolor=ft.Colors.RED_700,
            color=ft.Colors.WHITE,
            on_click=exportar,
        )

        largura_andar = 64
        largura_apto = 45
        largura_corredor = 58
        altura = 46
        linhas: list[ft.Control] = []
        cabecalho_colunas: list[ft.Control] = [
            ft.Container(
                width=largura_andar,
                height=34,
                content=ft.Text("Andar", weight=ft.FontWeight.BOLD),
                alignment=ft.Alignment.CENTER,
                bgcolor=ft.Colors.GREY_200,
                border=ft.Border.all(1, ft.Colors.GREY_500),
            )
        ]
        cabecalho_colunas.extend(
            ft.Container(
                width=largura_apto,
                height=34,
                content=ft.Text(f"{numero:02d}", weight=ft.FontWeight.BOLD),
                alignment=ft.Alignment.CENTER,
                bgcolor=ft.Colors.GREY_200,
                border=ft.Border.all(1, ft.Colors.GREY_500),
            )
            for numero in range(1, 15)
        )
        cabecalho_colunas.append(
            ft.Container(
                width=largura_corredor,
                height=34,
                content=ft.Text("Corr.", weight=ft.FontWeight.BOLD),
                alignment=ft.Alignment.CENTER,
                bgcolor=ft.Colors.GREY_200,
                border=ft.Border.all(1, ft.Colors.GREY_500),
            )
        )
        linhas.append(ft.Row(controls=cabecalho_colunas, spacing=0))

        andares = sorted(self.banco_dados["obras"][obra], key=chave_ordenacao_natural)
        for andar in andares:
            locais = self.banco_dados["obras"][obra].get(andar, {})
            celulas: list[ft.Control] = [
                ft.Container(
                    width=largura_andar,
                    height=altura,
                    content=ft.Text(str(andar), weight=ft.FontWeight.BOLD),
                    alignment=ft.Alignment.CENTER,
                    bgcolor=ft.Colors.GREY_100,
                    border=ft.Border.all(1, ft.Colors.GREY_500),
                )
            ]
            for numero in range(1, 15):
                local = _local_da_coluna(str(andar), numero, locais)
                dados = nova_atividade()
                if local is not None:
                    candidato = locais.get(local, {}).get(atividade, {})
                    dados = _dados_atividade_seguros(candidato)
                mostrar_obs = dados["status"] in {
                    "Em Andamento",
                    "Não Conforme",
                } and bool(dados["obs"].strip())
                celulas.append(
                    ft.Container(
                        width=largura_apto,
                        height=altura,
                        content=(
                            ft.Text(
                                dados["obs"],
                                size=7,
                                weight=ft.FontWeight.BOLD,
                                text_align=ft.TextAlign.CENTER,
                                max_lines=3,
                                overflow=ft.TextOverflow.ELLIPSIS,
                                color=ft.Colors.WHITE
                                if dados["status"] in {"Em Andamento", "Não Conforme"}
                                else ft.Colors.BLACK,
                            )
                            if mostrar_obs
                            else None
                        ),
                        alignment=ft.Alignment.CENTER,
                        padding=2,
                        bgcolor=STATUS_FLET_COLOR[dados["status"]],
                        border=ft.Border.all(1, ft.Colors.GREY_700),
                        tooltip=f"{dados['status']}\n{dados['obs']}",
                    )
                )

            corredor = next((nome for nome in locais if eh_corredor(nome)), None)
            dados_corredor = nova_atividade()
            if corredor is not None:
                dados_corredor = _dados_atividade_seguros(
                    locais.get(corredor, {}).get(atividade, {})
                )
            mostrar_obs_corr = dados_corredor["status"] in {
                "Em Andamento",
                "Não Conforme",
            } and bool(dados_corredor["obs"].strip())
            celulas.append(
                ft.Container(
                    width=largura_corredor,
                    height=altura,
                    content=(
                        ft.Text(
                            dados_corredor["obs"],
                            size=7,
                            weight=ft.FontWeight.BOLD,
                            text_align=ft.TextAlign.CENTER,
                            max_lines=3,
                            overflow=ft.TextOverflow.ELLIPSIS,
                            color=ft.Colors.WHITE
                            if dados_corredor["status"]
                            in {"Em Andamento", "Não Conforme"}
                            else ft.Colors.BLACK,
                        )
                        if mostrar_obs_corr
                        else None
                    ),
                    alignment=ft.Alignment.CENTER,
                    padding=2,
                    bgcolor=STATUS_FLET_COLOR[dados_corredor["status"]],
                    border=ft.Border.all(1, ft.Colors.GREY_700),
                    tooltip=f"{dados_corredor['status']}\n{dados_corredor['obs']}",
                )
            )
            linhas.append(ft.Row(controls=celulas, spacing=0))

        matriz = ft.Row(
            controls=[ft.Column(controls=linhas, spacing=0)],
            scroll=ft.ScrollMode.AUTO,
        )
        legenda = ft.Row(
            controls=[
                ft.Container(
                    content=ft.Row(
                        controls=[
                            ft.Container(
                                width=14,
                                height=14,
                                bgcolor=STATUS_FLET_COLOR[status],
                                border=ft.Border.all(1, ft.Colors.GREY_600),
                            ),
                            ft.Text(status, size=11),
                        ],
                        spacing=4,
                    )
                )
                for status in STATUS
            ],
            wrap=True,
            spacing=12,
            run_spacing=4,
        )
        self._definir_tela(
            cabecalho=cabecalho,
            corpo=ft.Column(
                controls=[botao_pdf, legenda, ft.Divider(), matriz],
                expand=True,
                scroll=ft.ScrollMode.AUTO,
            ),
        )


async def main(page: ft.Page) -> None:
    aplicativo = AppVistoria(page)
    await aplicativo.iniciar()


if __name__ == "__main__":
    ft.run(
        main,
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8000")),
        assets_dir="assets",
    )
