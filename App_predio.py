import flet as ft
import json
import urllib.request
import urllib.parse 
import os
import time       
import glob
import base64       
from fpdf import FPDF 
from fpdf.enums import XPos, YPos

FIREBASE_URL = "https://app-vistoria-986c3-default-rtdb.firebaseio.com/banco_dados.json"

# ==========================================
# COFRE DE SESSÃO 100% SEGURO (PYTHON PURO)
# ==========================================
ACTIVE_SESSIONS = {}

def main(page: ft.Page):
    page.title = "App de Vistoria"
    page.theme_mode = ft.ThemeMode.LIGHT
    page.padding = 20
    page.window.width = 420  
    page.window.height = 750

    # MOTOR DE FOTOGRAFIAS (FilePicker)
    file_picker = ft.FilePicker()
    page.overlay.append(file_picker)

    def on_upload_progress(e: ft.FilePickerUploadEvent):
        if e.progress == 1.0:
            caminho = os.path.join("assets", e.file_name)
            if os.path.exists(caminho):
                with open(caminho, "rb") as f:
                    b64 = base64.b64encode(f.read()).decode("utf-8")
                
                if hasattr(file_picker, "dados_alvo"):
                    file_picker.dados_alvo["foto"] = b64
                if hasattr(file_picker, "btn_alvo"):
                    file_picker.btn_alvo.icon = ft.Icons.CHECK
                    file_picker.btn_alvo.text = "Foto Anexada"
                    file_picker.btn_alvo.bgcolor = ft.Colors.GREEN_600
                page.update()
                
                try:
                    os.remove(caminho) 
                except: pass

    def on_dialog_result(e: ft.FilePickerResultEvent):
        if e.files and len(e.files) > 0:
            file_picker.upload(
                [
                    ft.FilePickerUploadFile(
                        e.files[0].name,
                        upload_url=page.get_upload_url(e.files[0].name, 60),
                    )
                ]
            )

    file_picker.on_result = on_dialog_result
    file_picker.on_upload = on_upload_progress

    lista_servicos_base = [
        "Revestimento piso banheiro", "Dreno", "Revestimento porcelanato", 
        "Limpeza", "Forro de gesso", "Forro da varanda", 
        "Ralos da varanda", "Forro do banheiro"
    ]

    # ==========================================
    # FUNÇÕES DE SINCRONIZAÇÃO E MIGRAÇÃO
    # ==========================================
    def carregar_do_firebase():
        try:
            req = urllib.request.Request(FIREBASE_URL)
            with urllib.request.urlopen(req) as response:
                dados = json.loads(response.read().decode())
                def consertar_listas(obj):
                    if isinstance(obj, list):
                        return {str(i): consertar_listas(v) for i, v in enumerate(obj) if v is not None}
                    elif isinstance(obj, dict):
                        return {k: consertar_listas(v) for k, v in obj.items()}
                    return obj
                return consertar_listas(dados)
        except Exception as e:
            return None

    def salvar_no_firebase(dados, mostrar_snack=True):
        try:
            req = urllib.request.Request(FIREBASE_URL, data=json.dumps(dados).encode('utf-8'), method='PUT')
            req.add_header('Content-Type', 'application/json')
            urllib.request.urlopen(req)
            if mostrar_snack:
                snack = ft.SnackBar(ft.Text("☁️ Sincronizado com o Firebase!", color=ft.Colors.WHITE), bgcolor=ft.Colors.GREEN_700)
                page.overlay.append(snack)
                snack.open = True
                page.update()
        except Exception as e:
            pass

    banco_dados = carregar_do_firebase()
    if not isinstance(banco_dados, dict):
        banco_dados = {"obras": {}, "usuarios": {"admin": {"senha": "123", "perfil": "admin", "nome": "Admin Principal"}}, "historico": []}
        salvar_no_firebase(banco_dados, mostrar_snack=False)
    else:
        precisa_migrar = False
        if "obras" not in banco_dados:
            banco_dados["obras"] = {}
            precisa_migrar = True
        if "usuarios" not in banco_dados:
            banco_dados["usuarios"] = {"admin": {"senha": "123", "perfil": "admin", "nome": "Admin Principal"}}
            precisa_migrar = True
        if "historico" not in banco_dados:
            banco_dados["historico"] = []
            precisa_migrar = True
        elif isinstance(banco_dados["historico"], dict):
            chaves = sorted(banco_dados["historico"].keys(), key=lambda x: int(x) if str(x).isdigit() else 9999)
            banco_dados["historico"] = [banco_dados["historico"][k] for k in chaves]
                
        if precisa_migrar:
            salvar_no_firebase(banco_dados, mostrar_snack=False)

    def get_cor_status(status):
        if status == "Finalizado": return ft.Colors.GREEN_500
        if status == "Não Conforme": return ft.Colors.RED_500
        if status == "Em Andamento": return ft.Colors.BLUE_500
        if status == "Existente": return ft.Colors.ORANGE_500
        return ft.Colors.GREY_400


    def registrar_historico(acao, detalhes):
        if "historico" not in banco_dados:
            banco_dados["historico"] = []
        elif isinstance(banco_dados["historico"], dict):
            chaves = sorted(banco_dados["historico"].keys(), key=lambda x: int(x) if str(x).isdigit() else 9999)
            banco_dados["historico"] = [banco_dados["historico"][k] for k in chaves]
            
        usuario_atual = ACTIVE_SESSIONS.get(page.session_id, {}).get("usuario") or "SISTEMA"
        hora_atual = time.strftime("%d/%m/%Y %H:%M")
        
        registro = {"data": hora_atual, "user": usuario_atual, "acao": acao, "detalhes": detalhes}
        banco_dados["historico"].insert(0, registro)
        if len(banco_dados["historico"]) > 300:
            banco_dados["historico"] = banco_dados["historico"][:300]


    # ==========================================
    # PDF: AGORA IMPRIME A OBSERVAÇÃO DENTRO DOS QUADRADOS
    # ==========================================
    def gerar_pdf(obra, servico_escolhido, andares_ordenados, caminho_arquivo):
        pdf = FPDF(orientation='L', unit='mm', format='A4') 
        pdf.set_auto_page_break(auto=True, margin=10)
        pdf.add_page()
        
        pdf.set_font("helvetica", 'B', 15)
        pdf.cell(0, 8, f"RELATÓRIO DE VISTORIA - {obra.upper()}", new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='C')
        pdf.set_font("helvetica", 'B', 11)
        pdf.cell(0, 6, f"Atividade: {servico_escolhido}", new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='C')
        pdf.ln(3)

        pdf.set_font("helvetica", 'B', 9)
        pdf.set_fill_color(76, 175, 80); pdf.cell(8, 5, "", border=1, fill=True, new_x=XPos.RIGHT, new_y=YPos.TOP); pdf.cell(14, 5, " OK", new_x=XPos.RIGHT, new_y=YPos.TOP)
        pdf.set_fill_color(244, 67, 54); pdf.cell(8, 5, "", border=1, fill=True, new_x=XPos.RIGHT, new_y=YPos.TOP); pdf.cell(20, 5, " Pend.", new_x=XPos.RIGHT, new_y=YPos.TOP)
        pdf.set_fill_color(33, 150, 243); pdf.cell(8, 5, "", border=1, fill=True, new_x=XPos.RIGHT, new_y=YPos.TOP); pdf.cell(22, 5, " Andam.", new_x=XPos.RIGHT, new_y=YPos.TOP)
        pdf.set_fill_color(255, 152, 0); pdf.cell(8, 5, "", border=1, fill=True, new_x=XPos.RIGHT, new_y=YPos.TOP); pdf.cell(22, 5, " Exist.", new_x=XPos.RIGHT, new_y=YPos.TOP)
        pdf.set_fill_color(189, 189, 189); pdf.cell(8, 5, "", border=1, fill=True, new_x=XPos.RIGHT, new_y=YPos.TOP); pdf.cell(28, 5, " Não Iniciado", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.ln(4)

        largura_andar = 20
        largura_apto = 16
        largura_corr = 22
        altura_celula = 7 

        largura_total = largura_andar + (14 * largura_apto) + largura_corr
        margem_esq = (297 - largura_total) / 2
        pdf.set_x(margem_esq)

        pdf.set_font("helvetica", 'B', 10)
        pdf.set_fill_color(230, 230, 230) 
        pdf.cell(largura_andar, altura_celula, "", border=0, new_x=XPos.RIGHT, new_y=YPos.TOP) 
        largura_horizontal = (14 * largura_apto) + largura_corr
        pdf.cell(largura_horizontal, altura_celula, "APARTAMENTOS ->", border=1, align='C', fill=True, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        
        pdf.set_x(margem_esq)
        pdf.set_font("helvetica", 'B', 10)
        pdf.cell(largura_andar, altura_celula, "Andar v", border=1, align='C', new_x=XPos.RIGHT, new_y=YPos.TOP)
        for i in range(1, 15):
            pdf.cell(largura_apto, altura_celula, f"{i:02d}", border=1, align='C', new_x=XPos.RIGHT, new_y=YPos.TOP)
        pdf.cell(largura_corr, altura_celula, "Corr.", border=1, align='C', new_x=XPos.LMARGIN, new_y=YPos.NEXT)

        for andar in andares_ordenados:
            pdf.set_x(margem_esq)
            pdf.set_font("helvetica", 'B', 10)
            pdf.cell(largura_andar, altura_celula, f"{andar}º", border=1, align='C', new_x=XPos.RIGHT, new_y=YPos.TOP)
            
            locais = [f"{andar}{apto:02d}" for apto in range(1, 15)] + ["Corredor"]
            for i, local in enumerate(locais):
                status = "Não Iniciado"
                obs_texto = ""
                
                if local in banco_dados["obras"][obra][andar] and servico_escolhido in banco_dados["obras"][obra][andar][local]:
                    dados_servico = banco_dados["obras"][obra][andar][local][servico_escolhido]
                    status = dados_servico.get("status", "Não Iniciado")
                    obs_texto = dados_servico.get("obs", "").replace('\n', ' ')

                if status == "Finalizado": pdf.set_fill_color(76, 175, 80)
                elif status == "Não Conforme": pdf.set_fill_color(244, 67, 54)
                elif status == "Em Andamento": pdf.set_fill_color(33, 150, 243)
                elif status == "Existente": pdf.set_fill_color(255, 152, 0)
                else: pdf.set_fill_color(189, 189, 189)

                # Prepara o texto para sair dentro do quadradinho
                texto_impresso = ""
                if status in ["Não Conforme", "Em Andamento"] and obs_texto:
                    texto_impresso = obs_texto[:9] # Pega as primeiras 9 letras para caber
                
                pdf.set_font("helvetica", 'B', 5.5)
                pdf.set_text_color(0, 0, 0) # Cor Preta para dar leitura

                larg = largura_corr if local == "Corredor" else largura_apto
                
                if i == len(locais) - 1:
                    pdf.cell(larg, altura_celula, txt=texto_impresso, border=1, fill=True, align='C', new_x=XPos.LMARGIN, new_y=YPos.NEXT)
                else:
                    pdf.cell(larg, altura_celula, txt=texto_impresso, border=1, fill=True, align='C', new_x=XPos.RIGHT, new_y=YPos.TOP)

        pdf.output(caminho_arquivo)


    # ==========================================
    # TELA DE GALERIA DE OBSERVAÇÕES
    # ==========================================
    def abrir_tela_observacoes(obra):
        page.floating_action_button = None 
        page.controls.clear()
        page.vertical_alignment = ft.MainAxisAlignment.START
        
        cabecalho = ft.Row([
            ft.IconButton(icon=ft.Icons.ARROW_BACK, icon_color=ft.Colors.BLUE_700, on_click=lambda _: abrir_tela_andares(obra)),
            ft.Text("Galeria de Observações", size=20, weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE_700)
        ])
        
        lista_galeria = ft.ListView(expand=True, spacing=15)
        
        tem_obs = False
        
        andares_ordenados = sorted(banco_dados["obras"][obra].keys(), key=lambda x: int(x) if str(x).isdigit() else 9999)
        for andar in andares_ordenados:
            for apto, servicos in banco_dados["obras"][obra][andar].items():
                for nome_servico, dados in servicos.items():
                    status = dados.get("status", "Não Iniciado")
                    obs = dados.get("obs", "")
                    foto = dados.get("foto", "")
                    
                    if status in ["Não Conforme", "Em Andamento"] and (obs or foto):
                        tem_obs = True
                        cor_borda = ft.Colors.RED_500 if status == "Não Conforme" else ft.Colors.BLUE_500
                        
                        conteudo_card = [
                            ft.Row([
                                ft.Text(f"{andar}º Andar - Apto {apto}", weight="bold", size=16),
                                ft.Text(status, color=cor_borda, weight="bold", size=12)
                            ], alignment="spaceBetween"),
                            ft.Text(f"Atividade: {nome_servico}", color=ft.Colors.GREY_700, size=13, weight="bold"),
                            ft.Divider()
                        ]
                        
                        if obs:
                            conteudo_card.append(ft.Text(f"📝 Obs: {obs}", size=14))
                        if foto:
                            conteudo_card.append(
                                ft.Container(
                                    content=ft.Image(src_base64=foto, fit=ft.ImageFit.COVER, border_radius=8),
                                    margin=ft.margin.only(top=10),
                                    border_radius=8, border=ft.border.all(1, ft.Colors.GREY_300)
                                )
                            )
                            
                        card = ft.Container(
                            content=ft.Column(conteudo_card),
                            bgcolor=ft.Colors.WHITE, padding=15, border_radius=10, 
                            border=ft.border.all(2, cor_borda)
                        )
                        lista_galeria.controls.append(card)

        if not tem_obs:
            lista_galeria.controls.append(
                ft.Container(
                    content=ft.Text("Nenhuma pendência ou observação fotográfica encontrada nesta obra.", text_align="center", color=ft.Colors.GREY_500),
                    padding=40, alignment=ft.alignment.center
                )
            )

        page.add(cabecalho, lista_galeria)
        page.update()


    def abrir_tela_dashboard(obra):
        # ... (Código do painel mantido igual) ...
        page.floating_action_button = None 
        page.controls.clear()
        page.vertical_alignment = ft.MainAxisAlignment.START
        
        cabecalho = ft.Row([
            ft.IconButton(icon=ft.Icons.ARROW_BACK, icon_color=ft.Colors.BLUE_700, on_click=lambda _: abrir_tela_andares(obra)),
            ft.Text("Métricas da Obra", size=20, weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE_700)
        ])

        try:
            total_tarefas = 0
            total_finalizado = 0
            total_andamento = 0
            total_conforme = 0
            total_existente = 0

            stats_atividade = {}

            if obra in banco_dados["obras"]:
                for andar, apartamentos in banco_dados["obras"][obra].items():
                    if isinstance(apartamentos, dict):  
                        for apto, atividades in apartamentos.items():
                            if isinstance(atividades, dict):  
                                for atividade, dados in atividades.items():
                                    if isinstance(dados, dict):  
                                        status = dados.get("status", "Não Iniciado")
                                        
                                        if atividade not in stats_atividade:
                                            stats_atividade[atividade] = {"total": 0, "finalizado": 0}
                                        
                                        stats_atividade[atividade]["total"] += 1
                                        if status == "Finalizado" or status == "Existente":
                                            stats_atividade[atividade]["finalizado"] += 1
                                            if status == "Finalizado":
                                                total_finalizado += 1
                                            else:
                                                total_existente += 1
                                        elif status == "Em Andamento":
                                            total_andamento += 1
                                        elif status == "Não Conforme":
                                            total_conforme += 1
                                        
                                        total_tarefas += 1

            pct_geral = ((total_finalizado + total_existente) / total_tarefas) if total_tarefas > 0 else 0

            card_progresso_geral = ft.Container(
                content=ft.Column(
                    [
                        ft.Text("PROGRESSO TOTAL CONCLUÍDO", size=11, weight=ft.FontWeight.BOLD, color=ft.Colors.GREY_600),
                        ft.Row(
                            [
                                ft.Text(f"{pct_geral * 100:.1f}%", size=34, weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE_800),
                                ft.Text("de metas atingidas", size=13, color=ft.Colors.GREY_600)
                            ], 
                            alignment=ft.MainAxisAlignment.START, 
                            vertical_alignment=ft.CrossAxisAlignment.END 
                        ),
                        ft.Container(
                            content=ft.ProgressBar(value=pct_geral, color=ft.Colors.BLUE_700, bgcolor=ft.Colors.GREY_200),
                            height=10
                        )
                    ], 
                    spacing=4
                ),
                bgcolor=ft.Colors.BLUE_50,
                padding=16,
                border_radius=10
            )

            grid_contadores = ft.Row(
                [
                    ft.Container(
                        content=ft.Column([
                            ft.Text("OK", size=10, weight=ft.FontWeight.BOLD, color=ft.Colors.GREEN_700),
                            ft.Text(str(total_finalizado), size=18, weight=ft.FontWeight.BOLD, color=ft.Colors.GREEN_700)
                        ], horizontal_alignment=ft.CrossAxisAlignment.CENTER),
                        bgcolor=ft.Colors.GREEN_50, padding=10, border_radius=8, expand=True
                    ),
                    ft.Container(
                        content=ft.Column([
                            ft.Text("ANDAM.", size=10, weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE_700),
                            ft.Text(str(total_andamento), size=18, weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE_700)
                        ], horizontal_alignment=ft.CrossAxisAlignment.CENTER),
                        bgcolor=ft.Colors.BLUE_50, padding=10, border_radius=8, expand=True
                    ),
                    ft.Container(
                        content=ft.Column([
                            ft.Text("PEND.", size=10, weight=ft.FontWeight.BOLD, color=ft.Colors.RED_700),
                            ft.Text(str(total_conforme), size=18, weight=ft.FontWeight.BOLD, color=ft.Colors.RED_700)
                        ], horizontal_alignment=ft.CrossAxisAlignment.CENTER),
                        bgcolor=ft.Colors.RED_50, padding=10, border_radius=8, expand=True
                    ),
                    ft.Container(
                        content=ft.Column([
                            ft.Text("EXIST.", size=10, weight=ft.FontWeight.BOLD, color=ft.Colors.ORANGE_700),
                            ft.Text(str(total_existente), size=18, weight=ft.FontWeight.BOLD, color=ft.Colors.ORANGE_700)
                        ], horizontal_alignment=ft.CrossAxisAlignment.CENTER),
                        bgcolor=ft.Colors.ORANGE_50, padding=10, border_radius=8, expand=True
                    )
                ], 
                spacing=6
            )

            lista_atividades_progresso = ft.ListView(expand=True, spacing=14)

            for ativ, dados_at in sorted(stats_atividade.items()):
                tot = dados_at["total"]
                fin = dados_at["finalizado"]
                pct_ativ = (fin / tot) if tot > 0 else 0
                
                item_progresso = ft.Column(
                    [
                        ft.Row(
                            [
                                ft.Text(ativ, size=13, weight=ft.FontWeight.W_600, color=ft.Colors.GREY_800, expand=True),
                                ft.Text(f"{fin}/{tot} ({pct_ativ * 100:.0f}%)", size=12, weight=ft.FontWeight.BOLD, color=ft.Colors.GREY_700)
                            ], 
                            alignment=ft.MainAxisAlignment.SPACE_BETWEEN
                        ),
                        ft.Container(
                            content=ft.ProgressBar(value=pct_ativ, color=ft.Colors.GREEN_500, bgcolor=ft.Colors.GREY_200),
                            height=6
                        )
                    ], 
                    spacing=3
                )
                lista_atividades_progresso.controls.append(item_progresso)

            page.add(
                cabecalho,
                card_progresso_geral,
                ft.Divider(height=10, color=ft.Colors.TRANSPARENT),
                grid_contadores,
                ft.Divider(height=15, color=ft.Colors.TRANSPARENT),
                ft.Text("EVOLUÇÃO POR ATIVIDADE", size=12, weight=ft.FontWeight.BOLD, color=ft.Colors.GREY_600),
                lista_atividades_progresso
            )
            page.update()

        except Exception as e:
            page.add(
                cabecalho,
                ft.Text("Aviso: Falha ao desenhar o painel. Um dado no Firebase pode estar corrompido.", color=ft.Colors.RED_500, weight=ft.FontWeight.BOLD),
                ft.Text(f"Log: {str(e)}", color=ft.Colors.GREY_600, size=10)
            )
            page.update()

    def abrir_tela_relatorio(obra, servico_escolhido):
        page.floating_action_button = None 
        page.controls.clear()
        page.vertical_alignment = ft.MainAxisAlignment.START

        cabecalho = ft.Row([
            ft.IconButton(icon=ft.Icons.ARROW_BACK, icon_color=ft.Colors.BLUE_700, on_click=lambda _: abrir_tela_andares(obra)),
            ft.Text(f"Relatório: {servico_escolhido}", size=18, weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE_700, expand=True)
        ])

        andares_ordenados = sorted(banco_dados["obras"][obra].keys(), key=lambda x: int(x) if str(x).isdigit() else 9999)
        bloco_botoes_acao = ft.Column(spacing=10, horizontal_alignment=ft.CrossAxisAlignment.CENTER)

        def acionar_pdf(e):
            try:
                botao_exportar.content.controls[1].value = "A Gerar Ficheiro..."
                page.update()
                
                if not os.path.exists("assets"):
                    os.makedirs("assets")
                
                padrao_busca = os.path.join("assets", f"Relatorio_{obra.replace(' ', '_')}_{servico_escolhido.replace(' ', '_')}*.pdf")
                for arquivo_antigo in glob.glob(padrao_busca):
                    try:
                        os.remove(arquivo_antigo)
                    except: pass
                    
                timestamp = int(time.time())
                nome_pdf = f"Relatorio_{obra.replace(' ', '_')}_{servico_escolhido.replace(' ', '_')}_{timestamp}.pdf"
                caminho_completo = os.path.join("assets", nome_pdf)

                gerar_pdf(obra, servico_escolhido, andares_ordenados, caminho_completo)
                url_segura = f"/{urllib.parse.quote(nome_pdf)}"
                
                botao_exportar.content.controls[1].value = "Gerar PDF (A4)"
                
                botao_download = ft.Container(
                    content=ft.Row(
                        [ft.Icon(ft.Icons.DOWNLOAD, color=ft.Colors.WHITE), ft.Text("CLIQUE AQUI PARA ABRIR O PDF", color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD, size=15)], 
                        alignment=ft.MainAxisAlignment.CENTER
                    ),
                    bgcolor=ft.Colors.BLUE_600, padding=15, border_radius=8, ink=True,
                    url=ft.Url(url=url_segura, target=ft.UrlTarget.SELF),
                )
                
                bloco_botoes_acao.controls.clear()
                bloco_botoes_acao.controls.append(botao_exportar)
                bloco_botoes_acao.controls.append(botao_download)
                page.update()
                
            except Exception as ex:
                botao_exportar.content.controls[1].value = "Gerar PDF (A4)"
                snack_erro = ft.SnackBar(ft.Text(f"Erro ao gerar PDF: {ex}"), bgcolor=ft.Colors.RED_700)
                page.overlay.append(snack_erro)
                snack_erro.open = True
                page.update()

        botao_exportar = ft.Container(
            content=ft.Row([ft.Icon(ft.Icons.PICTURE_AS_PDF, color=ft.Colors.WHITE), ft.Text("Gerar PDF (A4)", weight=ft.FontWeight.BOLD, color=ft.Colors.WHITE)], alignment=ft.MainAxisAlignment.CENTER),
            bgcolor=ft.Colors.RED_700, padding=12, border_radius=8, ink=True, on_click=acionar_pdf
        )

        bloco_botoes_acao.controls.append(botao_exportar)

        legenda = ft.Row([
            ft.Container(width=15, height=15, bgcolor=ft.Colors.GREEN_500, border_radius=3), ft.Text("OK", size=12),
            ft.Container(width=15, height=15, bgcolor=ft.Colors.RED_500, border_radius=3), ft.Text("Pend.", size=12),
            ft.Container(width=15, height=15, bgcolor=ft.Colors.BLUE_500, border_radius=3), ft.Text("Andam.", size=12),
            ft.Container(width=15, height=15, bgcolor=ft.Colors.ORANGE_500, border_radius=3), ft.Text("Existente", size=12),
            ft.Container(width=15, height=15, bgcolor=ft.Colors.GREY_400, border_radius=3), ft.Text("Não Iniciado", size=12),
        ], alignment=ft.MainAxisAlignment.CENTER, spacing=8)

        tabela = ft.Column(spacing=5)
        
        largura_celulas_horizontais = (35 * 14) + (5 * 14) + 45 
        
        linha_super_header = ft.Row([
            ft.Container(width=60), 
            ft.Container(
                width=largura_celulas_horizontais, 
                content=ft.Row([ft.Text("APARTAMENTOS E LOCAIS →", weight=ft.FontWeight.BOLD, size=11, color=ft.Colors.BLUE_900)], alignment=ft.MainAxisAlignment.CENTER),
                bgcolor=ft.Colors.BLUE_50, border_radius=4, padding=2
            )
        ], spacing=5)
        tabela.controls.append(linha_super_header)

        linha_header = ft.Row([ft.Container(width=60, content=ft.Row([ft.Text("Andar ↓", weight=ft.FontWeight.BOLD, color=ft.Colors.GREY_700)], alignment=ft.MainAxisAlignment.START))], spacing=5)
        for apto_num in range(1, 15):
            linha_header.controls.append(ft.Container(width=35, content=ft.Row([ft.Text(f"{apto_num:02d}", weight=ft.FontWeight.BOLD, size=12, color=ft.Colors.GREY_700)], alignment=ft.MainAxisAlignment.CENTER)))
        linha_header.controls.append(ft.Container(width=45, content=ft.Row([ft.Text("Corr.", weight=ft.FontWeight.BOLD, size=12, color=ft.Colors.GREY_700)], alignment=ft.MainAxisAlignment.CENTER)))
        tabela.controls.append(linha_header)

        for andar in andares_ordenados:
            linha_andar = ft.Row([ft.Container(width=60, content=ft.Row([ft.Text(f"{andar}º", weight=ft.FontWeight.BOLD)], alignment=ft.MainAxisAlignment.START))], spacing=5)
            
            locais_matriz = [f"{andar}{apto_num:02d}" for apto_num in range(1, 15)] + ["Corredor"]
            
            for local_str in locais_matriz:
                status_atual = "Não Iniciado" 
                if local_str in banco_dados["obras"][obra][andar] and servico_escolhido in banco_dados["obras"][obra][andar][local_str]:
                    status_atual = banco_dados["obras"][obra][andar][local_str][servico_escolhido]["status"]
                
                cor_quadrado = get_cor_status(status_atual)
                largura_celula = 45 if local_str == "Corredor" else 35
                
                quadrado = ft.Container(width=largura_celula, height=35, bgcolor=cor_quadrado, border_radius=4, tooltip=f"{local_str}: {status_atual}")
                linha_andar.controls.append(quadrado)
                
            tabela.controls.append(linha_andar)

        area_rolagem = ft.Row([ft.Column([tabela], scroll=ft.ScrollMode.AUTO)], scroll=ft.ScrollMode.AUTO, expand=True)

        page.add(cabecalho, bloco_botoes_acao, legenda, ft.Divider(height=10, color=ft.Colors.TRANSPARENT), area_rolagem)


    def abrir_tela_lancamento_status(obra):
        # ... (Código do lançamento rápido mantido) ...
        page.floating_action_button = None 
        page.controls.clear()
        page.vertical_alignment = ft.MainAxisAlignment.START
        
        cabecalho = ft.Row([
            ft.IconButton(icon=ft.Icons.ARROW_BACK, icon_color=ft.Colors.BLUE_700, on_click=lambda _: abrir_tela_andares(obra)),
            ft.Text("Status Rápido", size=20, weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE_700)
        ])

        servicos_disponiveis = set(lista_servicos_base)
        for and_dados in banco_dados["obras"][obra].values():
            for ap_dados in and_dados.values():
                for s in ap_dados.keys():
                    servicos_disponiveis.add(s)
        
        opcoes_tarefas = [ft.dropdown.Option(s) for s in sorted(servicos_disponiveis)]
        dropdown_tarefa = ft.Dropdown(label="Qual Atividade?", options=opcoes_tarefas, expand=True)
        
        dropdown_status = ft.Dropdown(
            label="Status a Aplicar",
            options=[
                ft.dropdown.Option("Finalizado"),
                ft.dropdown.Option("Em Andamento"),
                ft.dropdown.Option("Não Conforme"),
                ft.dropdown.Option("Existente"),
                ft.dropdown.Option("Não Iniciado"),
            ],
            value="Finalizado",
            expand=True
        )

        andares_ordenados = sorted(banco_dados["obras"][obra].keys(), key=lambda x: int(x) if str(x).isdigit() else 9999)
        andar_inicial = andares_ordenados[0] if andares_ordenados else None
        
        opcoes_andares = [ft.dropdown.Option(key=str(a), text=f"{a}º Pavimento") for a in andares_ordenados]
        dropdown_andar = ft.Dropdown(label="Qual Andar?", options=opcoes_andares, value=str(andar_inicial), expand=True)

        aptos_selecionados = set() 
        grid_aptos = ft.GridView(expand=True, runs_count=4, child_aspect_ratio=1.0, spacing=10, run_spacing=10)

        def desenhar_grid():
            grid_aptos.controls.clear()
            andar_alvo = dropdown_andar.value 
            if not andar_alvo: return
            
            tarefa_atual = dropdown_tarefa.value
            aptos_do_andar = sorted(banco_dados["obras"][obra][andar_alvo].keys(), key=lambda x: int(x) if str(x).isdigit() else 9999)
            
            for apto in aptos_do_andar:
                is_selected = apto in aptos_selecionados
                
                cor_fundo = ft.Colors.GREY_300
                if tarefa_atual and tarefa_atual in banco_dados["obras"][obra][andar_alvo][apto]:
                    st = banco_dados["obras"][obra][andar_alvo][apto][tarefa_atual]["status"]
                    cor_fundo = get_cor_status(st)
                
                icone = ft.Icons.CHECK_CIRCLE if is_selected else None
                opacidade = 1.0 if is_selected else (0.5 if aptos_selecionados else 1.0)
                
                def criar_handler_clique(ap_nome=apto):
                    def toggle_selecao(e):
                        if ap_nome in aptos_selecionados:
                            aptos_selecionados.remove(ap_nome)
                        else:
                            aptos_selecionados.add(ap_nome)
                        desenhar_grid()
                    return toggle_selecao
                
                bloco = ft.Container(
                    content=ft.Column(
                        [
                            ft.Icon(icone, color=ft.Colors.WHITE, size=24) if icone else ft.Container(height=24),
                            ft.Text(apto, size=18, weight=ft.FontWeight.BOLD, color=ft.Colors.WHITE)
                        ], 
                        alignment=ft.MainAxisAlignment.CENTER, 
                        horizontal_alignment=ft.CrossAxisAlignment.CENTER
                    ),
                    bgcolor=cor_fundo, 
                    border_radius=8, 
                    opacity=opacidade,
                    ink=True, 
                    on_click=criar_handler_clique(apto)
                )
                grid_aptos.controls.append(bloco)
            page.update()

        dropdown_tarefa.on_change = lambda _: desenhar_grid()

        def mudar_andar(e):
            aptos_selecionados.clear() 
            desenhar_grid()
            
        dropdown_andar.on_change = mudar_andar

        def aplicar_status_lote(e):
            andar_alvo = dropdown_andar.value 
            tarefa = dropdown_tarefa.value
            if not tarefa:
                dropdown_tarefa.error_text = "Selecione uma atividade!"
                page.update()
                return
            if not aptos_selecionados:
                snack = ft.SnackBar(ft.Text("Selecione ao menos um apartamento!"), bgcolor=ft.Colors.RED_700)
                page.overlay.append(snack)
                snack.open = True
                page.update()
                return
            
            status_escolhido = dropdown_status.value
            
            for apt_sel in aptos_selecionados:
                if andar_alvo in banco_dados["obras"][obra] and apt_sel in banco_dados["obras"][obra][andar_alvo]:
                    if tarefa not in banco_dados["obras"][obra][andar_alvo][apt_sel]:
                        banco_dados["obras"][obra][andar_alvo][apt_sel][tarefa] = {"status": status_escolhido, "obs": "", "foto": ""}
                    else:
                        banco_dados["obras"][obra][andar_alvo][apt_sel][tarefa]["status"] = status_escolhido
                        if status_escolhido == "Finalizado":
                            banco_dados["obras"][obra][andar_alvo][apt_sel][tarefa]["obs"] = ""
                            banco_dados["obras"][obra][andar_alvo][apt_sel][tarefa]["foto"] = ""
            
            registrar_historico("Status em Lote", f"[{obra}] - Aplicou '{status_escolhido}' na ativ. '{tarefa}' em {len(aptos_selecionados)} locais do {andar_alvo}º andar.")
            
            salvar_no_firebase(banco_dados, mostrar_snack=False)
            
            snack = ft.SnackBar(ft.Text(f"✅ Status '{status_escolhido}' aplicado com sucesso!"), bgcolor=ft.Colors.GREEN_700)
            page.overlay.append(snack)
            snack.open = True
            
            aptos_selecionados.clear()
            desenhar_grid()

        botao_aplicar = ft.Container(
            content=ft.Row([ft.Icon(ft.Icons.DONE_ALL, color=ft.Colors.WHITE), ft.Text("ATUALIZAR APARTAMENTOS", color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD, size=15)], alignment=ft.MainAxisAlignment.CENTER),
            bgcolor=ft.Colors.ORANGE_700, padding=15, border_radius=8, ink=True, on_click=aplicar_status_lote
        )

        layout = ft.Column([
            ft.Row([dropdown_tarefa, dropdown_status]),
            dropdown_andar,
            ft.Divider(),
            ft.Text("Toque nos apartamentos para atualizar:", size=12, weight=ft.FontWeight.BOLD, color=ft.Colors.GREY_600),
            ft.Container(content=grid_aptos, expand=True)
        ], expand=True)

        page.add(cabecalho, layout, botao_aplicar)
        desenhar_grid()


    def abrir_tela_lancamento_tarefas(obra):
        page.floating_action_button = None 
        page.controls.clear()
        page.vertical_alignment = ft.MainAxisAlignment.START
        
        cabecalho = ft.Row([
            ft.IconButton(icon=ft.Icons.ARROW_BACK, icon_color=ft.Colors.BLUE_700, on_click=lambda _: abrir_tela_andares(obra)),
            ft.Text("Distribuir Tarefa", size=20, weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE_700)
        ])

        servicos_disponiveis = set(lista_servicos_base)
        for and_dados in banco_dados["obras"][obra].values():
            for ap_dados in and_dados.values():
                for s in ap_dados.keys():
                    servicos_disponiveis.add(s)
        
        opcoes_tarefas = [ft.dropdown.Option(s) for s in sorted(servicos_disponiveis)]
        dropdown_tarefa = ft.Dropdown(label="Escolha a Atividade", options=opcoes_tarefas, expand=True)

        def popup_nova_tarefa(e):
            def add_nova(e):
                val = campo_nova.value.strip().replace(".", "")
                if val:
                    dropdown_tarefa.options.append(ft.dropdown.Option(val))
                    dropdown_tarefa.value = val
                    dlg_nova.open = False
                    page.update()
                    
            campo_nova = ft.TextField(label="Digite o Nome da Nova Atividade", on_submit=add_nova)
            dlg_nova = ft.AlertDialog(
                title=ft.Text("Nova Atividade"), content=campo_nova, actions=[ft.TextButton("Adicionar", on_click=add_nova)]
            )
            page.overlay.append(dlg_nova)
            dlg_nova.open = True
            page.update()
            
        btn_nova_tarefa = ft.IconButton(ft.Icons.ADD_CIRCLE, icon_color=ft.Colors.GREEN_600, icon_size=40, on_click=popup_nova_tarefa)
        linha_tarefa = ft.Row([dropdown_tarefa, btn_nova_tarefa])

        andares_ordenados = sorted(banco_dados["obras"][obra].keys(), key=lambda x: int(x) if str(x).isdigit() else 9999)

        checkboxes_andares = {}
        grid_andares = ft.GridView(expand=True, runs_count=2, child_aspect_ratio=4.5, spacing=4, run_spacing=4)

        for andar in andares_ordenados:
            label_andar = f"{andar}º Andar" if str(andar).isdigit() else str(andar)
            cb = ft.Checkbox(label=label_andar, value=False)
            checkboxes_andares[andar] = cb
            grid_andares.controls.append(cb)

        def selecionar_todos_andares(e):
            todos_marcados = all(cb.value for cb in checkboxes_andares.values())
            for cb in checkboxes_andares.values():
                cb.value = not todos_marcados
            page.update()

        btn_selecionar_todos = ft.TextButton("Selecionar Todos", icon=ft.Icons.SELECT_ALL, on_click=selecionar_todos_andares)

        def aplicar_tarefa_lote(e):
            tarefa = dropdown_tarefa.value
            if not tarefa:
                dropdown_tarefa.error_text = "Selecione ou crie uma!"
                page.update()
                return
            
            andares_selecionados = [andar for andar, cb in checkboxes_andares.items() if cb.value]
            if not andares_selecionados:
                snack = ft.SnackBar(ft.Text("Marque pelo menos um Andar!"), bgcolor=ft.Colors.RED_700)
                page.overlay.append(snack)
                snack.open = True
                page.update()
                return
            
            for andar_alvo in andares_selecionados:
                for apto in banco_dados["obras"][obra][andar_alvo].keys():
                    if tarefa not in banco_dados["obras"][obra][andar_alvo][apto]:
                        banco_dados["obras"][obra][andar_alvo][apto][tarefa] = {"status": "Não Iniciado", "obs": "", "foto": ""}
            
            registrar_historico("Criou Tarefa Lote", f"[{obra}] - Injetou '{tarefa}' em {len(andares_selecionados)} andares.")
            
            salvar_no_firebase(banco_dados, mostrar_snack=False)
            
            snack = ft.SnackBar(ft.Text(f"✅ Tarefa '{tarefa}' adicionada em {len(andares_selecionados)} andares!"), bgcolor=ft.Colors.PURPLE_700)
            page.overlay.append(snack)
            snack.open = True
            
            for cb in checkboxes_andares.values():
                cb.value = False
            page.update()

        botao_aplicar = ft.Container(
            content=ft.Row([ft.Icon(ft.Icons.DONE_ALL, color=ft.Colors.WHITE), ft.Text("DISTRIBUIR TAREFA", color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD, size=15)], alignment=ft.MainAxisAlignment.CENTER),
            bgcolor=ft.Colors.PURPLE_700, padding=15, border_radius=8, ink=True, on_click=aplicar_tarefa_lote
        )

        layout = ft.Column([
            linha_tarefa,
            ft.Text("A nova tarefa será criada como 'Não Iniciado'.", size=11, color=ft.Colors.GREY_600),
            ft.Divider(),
            ft.Row([ft.Text("ONDE ELA DEVE APARECER?", size=12, weight=ft.FontWeight.BOLD, color=ft.Colors.GREY_600), btn_selecionar_todos], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
            ft.Container(content=grid_andares, expand=True)
        ], expand=True)

        page.add(cabecalho, layout, botao_aplicar)


    def abrir_tela_remover_tarefas(obra):
        page.floating_action_button = None 
        page.controls.clear()
        page.vertical_alignment = ft.MainAxisAlignment.START
        
        cabecalho = ft.Row([
            ft.IconButton(icon=ft.Icons.ARROW_BACK, icon_color=ft.Colors.BLUE_700, on_click=lambda _: abrir_tela_andares(obra)),
            ft.Text("Remover Tarefas", size=18, weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE_700)
        ])

        servicos_disponiveis = set(lista_servicos_base)
        for and_dados in banco_dados["obras"][obra].values():
            for ap_dados in and_dados.values():
                for s in ap_dados.keys():
                    servicos_disponiveis.add(s)
        
        opcoes_tarefas = [ft.dropdown.Option(s) for s in sorted(servicos_disponiveis)]
        dropdown_tarefa = ft.Dropdown(label="Escolha a Atividade a Excluir", options=opcoes_tarefas, expand=True)

        andares_ordenados = sorted(banco_dados["obras"][obra].keys(), key=lambda x: int(x) if str(x).isdigit() else 9999)

        checkboxes_andares = {}
        grid_andares = ft.GridView(expand=True, runs_count=2, child_aspect_ratio=4.5, spacing=4, run_spacing=4)

        for andar in andares_ordenados:
            label_andar = f"{andar}º Andar" if str(andar).isdigit() else str(andar)
            cb = ft.Checkbox(label=label_andar, value=False)
            checkboxes_andares[andar] = cb
            grid_andares.controls.append(cb)

        def selecionar_todos_andares(e):
            todos_marcados = all(cb.value for cb in checkboxes_andares.values())
            for cb in checkboxes_andares.values():
                cb.value = not todos_marcados
            page.update()

        btn_selecionar_todos = ft.TextButton("Selecionar Todos", icon=ft.Icons.SELECT_ALL, on_click=selecionar_todos_andares)

        def aplicar_remocao_lote(e):
            tarefa = dropdown_tarefa.value
            if not tarefa:
                dropdown_tarefa.error_text = "Selecione uma atividade!"
                page.update()
                return
            
            andares_selecionados = [andar for andar, cb in checkboxes_andares.items() if cb.value]
            if not andares_selecionados:
                snack = ft.SnackBar(ft.Text("Marque pelo menos um Andar!"), bgcolor=ft.Colors.RED_700)
                page.overlay.append(snack)
                snack.open = True
                page.update()
                return
            
            for andar_alvo in andares_selecionados:
                for apto in list(banco_dados["obras"][obra][andar_alvo].keys()):
                    if tarefa in banco_dados["obras"][obra][andar_alvo][apto]:
                        del banco_dados["obras"][obra][andar_alvo][apto][tarefa]
            
            registrar_historico("Removeu Tarefa Lote", f"[{obra}] - Apagou '{tarefa}' de {len(andares_selecionados)} andares.")
            
            salvar_no_firebase(banco_dados, mostrar_snack=False)
            
            snack = ft.SnackBar(ft.Text(f"❌ Tarefa '{tarefa}' apagada de {len(andares_selecionados)} andares!"), bgcolor=ft.Colors.RED_700)
            page.overlay.append(snack)
            snack.open = True
            
            for cb in checkboxes_andares.values():
                cb.value = False
            page.update()

        botao_aplicar = ft.Container(
            content=ft.Row([ft.Icon(ft.Icons.DELETE_FOREVER, color=ft.Colors.WHITE), ft.Text("REMOVER ATIVIDADE DO ESCOPO", color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD, size=14)], alignment=ft.MainAxisAlignment.CENTER),
            bgcolor=ft.Colors.RED_800, padding=15, border_radius=8, ink=True, on_click=aplicar_remocao_lote
        )

        layout = ft.Column([
            dropdown_tarefa,
            ft.Text("Atenção: Esta ação excluirá permanentemente a tarefa e o seu histórico nos andares marcados.", size=11, color=ft.Colors.RED_500),
            ft.Divider(),
            ft.Row([ft.Text("REMOVER DE QUAIS PAVIMENTOS?", size=12, weight=ft.FontWeight.BOLD, color=ft.Colors.GREY_600), btn_selecionar_todos], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
            ft.Container(content=grid_andares, expand=True)
        ], expand=True)

        page.add(cabecalho, layout, botao_aplicar)


    # ==========================================
    # TELA 4: CHECKLIST INDIVIDUAL DO APTO (COM FOTO)
    # ==========================================
    def abrir_tela_atividades(obra, andar, apto):
        page.floating_action_button = None 
        page.controls.clear()
        page.vertical_alignment = ft.MainAxisAlignment.START
        nome_tela = apto if apto == "Corredor" else f"Apto {apto}"
        
        perfil_user = ACTIVE_SESSIONS.get(page.session_id, {}).get("perfil")

        cabecalho = ft.Row([
            ft.IconButton(icon=ft.Icons.ARROW_BACK, icon_color=ft.Colors.BLUE_700, on_click=lambda _: abrir_tela_apartamentos(obra, andar)),
            ft.Text(nome_tela, size=22, weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE_700)
        ])

        container_botoes = ft.Row(wrap=True, spacing=10, run_spacing=10)
        area_rolagem = ft.Column([container_botoes], scroll=ft.ScrollMode.AUTO, expand=True)

        def confirmar_exclusao_ativ(nome_servico):
            def deletar(e):
                del banco_dados["obras"][obra][andar][apto][nome_servico]
                registrar_historico("Excluiu Atividade", f"[{obra}] - Apagou '{nome_servico}' no {andar}º > {apto}.")
                salvar_no_firebase(banco_dados) 
                dlg.open = False
                desenhar_botoes_atividades()
                page.update()
            dlg = ft.AlertDialog(title=ft.Text("Excluir Atividade"), content=ft.Text(f"Deseja excluir '{nome_servico}'?"), actions=[ft.TextButton("Cancelar", on_click=lambda e: fechar_dlg(dlg)), ft.TextButton("Excluir", on_click=deletar, style=ft.ButtonStyle(color=ft.Colors.RED))])
            page.overlay.append(dlg)
            dlg.open = True
            page.update()

        def fechar_dlg(dlg):
            dlg.open = False
            page.update()

        def desenhar_botoes_atividades():
            container_botoes.controls.clear()
            for nome_servico, dados in list(banco_dados["obras"][obra][andar][apto].items()):
                cor_botao = get_cor_status(dados["status"])
                
                # Mostra um pequeno ícone se tiver foto ou observação (Apenas visualmente rico)
                icones_extras = []
                if dados.get("obs"): icones_extras.append(ft.Icon(ft.Icons.NOTES, size=12, color=ft.Colors.WHITE70))
                if dados.get("foto"): icones_extras.append(ft.Icon(ft.Icons.CAMERA_ALT, size=12, color=ft.Colors.WHITE70))
                
                conteudo_btn = ft.Row([ft.Text(nome_servico, size=14, weight=ft.FontWeight.W_600, color=ft.Colors.WHITE)] + icones_extras)

                if perfil_user == "visualizador":
                    botao_atividade = ft.Container(content=conteudo_btn, bgcolor=cor_botao, padding=12, border_radius=20)
                else:
                    botao_atividade = ft.Container(
                        content=conteudo_btn, bgcolor=cor_botao, padding=12, border_radius=20, ink=True,
                        on_click=lambda e, s=nome_servico: abrir_popup_status(s), on_long_press=lambda e, s=nome_servico: confirmar_exclusao_ativ(s) 
                    )
                container_botoes.controls.append(botao_atividade)
            page.update()

        def abrir_popup_status(nome_servico):
            dados_atuais = banco_dados["obras"][obra][andar][apto][nome_servico]
            status_inicial = dados_atuais.get("status", "Não Iniciado")
            
            menu_dropdown = ft.Dropdown(
                options=[
                    ft.dropdown.Option("Não Iniciado"), 
                    ft.dropdown.Option("Em Andamento"), 
                    ft.dropdown.Option("Finalizado"), 
                    ft.dropdown.Option("Não Conforme"),
                    ft.dropdown.Option("Existente")
                ], 
                value=status_inicial, width=250
            )
            
            campo_obs = ft.TextField(label="Observação (sai no PDF)", value=dados_atuais.get("obs", ""), multiline=True)
            
            # Botão de Anexo de Foto
            tem_foto = bool(dados_atuais.get("foto", ""))
            btn_foto = ft.ElevatedButton(
                text="Foto Anexada" if tem_foto else "Anexar Fotografia", 
                icon=ft.Icons.CHECK if tem_foto else ft.Icons.CAMERA_ALT,
                bgcolor=ft.Colors.GREEN_600 if tem_foto else ft.Colors.BLUE_GREY_100,
                color=ft.Colors.WHITE if tem_foto else ft.Colors.BLACK87,
                on_click=lambda e: file_picker.pick_files()
            )
            
            # Ligação com o FilePicker invisível da página
            file_picker.dados_alvo = dados_atuais
            file_picker.btn_alvo = btn_foto

            # Mostra as opções apenas para Pendente ou Andamento
            bloco_extras = ft.Column([campo_obs, btn_foto], visible=(status_inicial in ["Não Conforme", "Em Andamento"]))
            
            def ao_mudar_dropdown(e):
                if menu_dropdown.value in ["Não Conforme", "Em Andamento"]:
                    bloco_extras.visible = True
                else:
                    bloco_extras.visible = False
                page.update()
                
            menu_dropdown.on_change = ao_mudar_dropdown

            def salvar_popup(e):
                novo_status = menu_dropdown.value
                
                # Se mudou para Finalizado, EXCLUI a foto e observação automaticamente
                if novo_status == "Finalizado":
                    dados_atuais["obs"] = ""
                    dados_atuais["foto"] = ""
                else:
                    dados_atuais["obs"] = campo_obs.value
                
                if dados_atuais["status"] != novo_status or dados_atuais["obs"] != campo_obs.value:
                    registrar_historico("Editou Status", f"[{obra}] - {andar}º Andar > {apto} > {nome_servico} agora é '{novo_status}'.")
                
                dados_atuais["status"] = novo_status
                salvar_no_firebase(banco_dados) 
                
                janela_popup.open = False
                desenhar_botoes_atividades()
                page.update()

            janela_popup = ft.AlertDialog(
                title=ft.Text(f"{nome_servico}"), 
                content=ft.Column([menu_dropdown, bloco_extras], tight=True), 
                actions=[ft.Container(content=ft.Text("Salvar", color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD), bgcolor=ft.Colors.BLUE_700, padding=10, border_radius=8, ink=True, on_click=salvar_popup)]
            )
            page.overlay.append(janela_popup)
            janela_popup.open = True
            page.update()

        def add_nova_atividade(e):
            nova_ativ = campo_nova.value.strip().replace(".", "") 
            if nova_ativ and nova_ativ not in banco_dados["obras"][obra][andar][apto]:
                banco_dados["obras"][obra][andar][apto][nova_ativ] = {"status": "Não Iniciado", "obs": "", "foto": ""}
                registrar_historico("Nova Ativ. Individual", f"[{obra}] - Criou '{nova_ativ}' no {andar}º > {apto}.")
                salvar_no_firebase(banco_dados) 
                campo_nova.value = ""
                desenhar_botoes_atividades()
                
        campo_nova = ft.TextField(label="Nova Atividade", expand=True, height=50, on_submit=add_nova_atividade)
        
        linha_add = ft.Row([campo_nova, ft.IconButton(ft.Icons.ADD_CIRCLE, icon_color=ft.Colors.GREEN_600, icon_size=35, on_click=add_nova_atividade)])
        linha_add.visible = (perfil_user in ["admin", "editor"]) 
        
        page.add(cabecalho, area_rolagem, ft.Divider(), linha_add)
        desenhar_botoes_atividades()


    # ==========================================
    # TELA 3: GRID DE APARTAMENTOS DO ANDAR
    # ==========================================
    def abrir_tela_apartamentos(obra, andar):
        page.floating_action_button = None 
        page.controls.clear()
        page.vertical_alignment = ft.MainAxisAlignment.START
        
        perfil_user = ACTIVE_SESSIONS.get(page.session_id, {}).get("perfil")

        cabecalho = ft.Row([ft.IconButton(icon=ft.Icons.ARROW_BACK, icon_color=ft.Colors.BLUE_700, on_click=lambda _: abrir_tela_andares(obra)), ft.Text(f"{andar}º Pavimento", size=22, weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE_700)])
        grid_aptos = ft.GridView(expand=True, runs_count=3, max_extent=110, child_aspect_ratio=1.0, spacing=15, run_spacing=15)

        def confirmar_exclusao_apto(apto_nome):
            def deletar(e):
                del banco_dados["obras"][obra][andar][apto_nome]
                salvar_no_firebase(banco_dados) 
                dlg.open = False
                desenhar_grid()
                page.update()
            dlg = ft.AlertDialog(title=ft.Text("Excluir Local"), content=ft.Text(f"Deseja excluir: {apto_nome}?"), actions=[ft.TextButton("Cancelar", on_click=lambda e: fechar_dlg(dlg)), ft.TextButton("Excluir", on_click=deletar, style=ft.ButtonStyle(color=ft.Colors.RED))])
            page.overlay.append(dlg)
            dlg.open = True
            page.update()

        def fechar_dlg(dlg):
            dlg.open = False
            page.update()

        def desenhar_grid():
            grid_aptos.controls.clear()
            aptos_ordenados = sorted(banco_dados["obras"][obra][andar].items(), key=lambda x: int(x[0]) if str(x[0]).isdigit() else 9999)
            
            for numero_apto, atividades in aptos_ordenados:
                cor_fundo = ft.Colors.GREY_400 
                if atividades: 
                    status_das_atividades = [dados["status"] for dados in atividades.values()]
                    if "Não Conforme" in status_das_atividades: 
                        cor_fundo = ft.Colors.RED_500 
                    elif all(status == "Finalizado" or status == "Existente" for status in status_das_atividades): 
                        if all(status == "Existente" for status in status_das_atividades):
                            cor_fundo = ft.Colors.ORANGE_500
                        else:
                            cor_fundo = ft.Colors.GREEN_500 
                    elif any(status != "Não Iniciado" for status in status_das_atividades): 
                        cor_fundo = ft.Colors.BLUE_500 
                
                tamanho_fonte = 18 if numero_apto == "Corredor" else 26
                
                bloco = ft.Container(
                    content=ft.Column(
                        [
                            ft.Text(value=numero_apto, size=tamanho_fonte, weight=ft.FontWeight.W_500, color=ft.Colors.WHITE), 
                            ft.Icon(ft.Icons.APPS, color=ft.Colors.WHITE70, size=24)
                        ], 
                        alignment=ft.MainAxisAlignment.CENTER, 
                        horizontal_alignment=ft.CrossAxisAlignment.CENTER
                    ), 
                    bgcolor=cor_fundo, 
                    border_radius=10, 
                    ink=True, 
                    on_click=lambda e, o=obra, a=andar, apt=numero_apto: abrir_tela_atividades(o, a, apt), 
                    on_long_press=(lambda e, apt=numero_apto: confirmar_exclusao_apto(apt)) if perfil_user == "admin" else None
                )
                grid_aptos.controls.append(bloco)
            page.update()

        def add_novo_apto(e):
            novo_apto = campo_novo_apto.value.strip().replace(".", "")
            if novo_apto and novo_apto not in banco_dados["obras"][obra][andar]:
                banco_dados["obras"][obra][andar][novo_apto] = {s: {"status": "Não Iniciado", "obs": ""} for s in lista_servicos_base}
                salvar_no_firebase(banco_dados) 
                campo_novo_apto.value = ""
                desenhar_grid()
                
        campo_novo_apto = ft.TextField(label="Novo Apto/Local", expand=True, height=50, on_submit=add_novo_apto)
        
        linha_add = ft.Row([campo_novo_apto, ft.IconButton(ft.Icons.ADD_CIRCLE, icon_color=ft.Colors.GREEN_600, icon_size=35, on_click=add_novo_apto)])
        linha_add.visible = (perfil_user in ["admin", "editor"])
        
        page.add(cabecalho, grid_aptos, ft.Divider(), linha_add)
        desenhar_grid()


    # ==========================================
    # TELA 2: NAVEGAÇÃO DE ANDARES + MENU FAB
    # ==========================================
    def abrir_tela_andares(obra):
        page.controls.clear()
        page.vertical_alignment = ft.MainAxisAlignment.START
        
        perfil_user = ACTIVE_SESSIONS.get(page.session_id, {}).get("perfil")
        
        cabecalho = ft.Row([
            ft.IconButton(icon=ft.Icons.ARROW_BACK, icon_color=ft.Colors.BLUE_700, on_click=lambda _: abrir_tela_obras()),
            ft.Text(f"{obra}", size=22, weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE_700, expand=True)
        ])
        
        def iniciar_relatorio():
            servicos_disponiveis = set(lista_servicos_base)
            for andar_dados in banco_dados["obras"][obra].values():
                for apto_dados in andar_dados.values():
                    for serv in apto_dados.keys():
                        servicos_disponiveis.add(serv)
            
            opcoes = [ft.dropdown.Option(s) for s in sorted(servicos_disponiveis)]
            menu_relatorio = ft.Dropdown(options=opcoes, label="Escolha a Atividade", width=250)
            
            def gerar(e):
                if menu_relatorio.value:
                    dlg_rel.open = False
                    abrir_tela_relatorio(obra, menu_relatorio.value)
                else:
                    menu_relatorio.error_text = "Selecione uma opção"
                    page.update()

            dlg_rel = ft.AlertDialog(
                title=ft.Text("Relatório"),
                content=ft.Column([menu_relatorio], tight=True),
                actions=[
                    ft.TextButton("Cancelar", on_click=lambda e: [setattr(dlg_rel, 'open', False), page.update()]), 
                    ft.TextButton("Gerar Visão", on_click=gerar, style=ft.ButtonStyle(color=ft.Colors.BLUE_700))
                ]
            )
            page.overlay.append(dlg_rel)
            dlg_rel.open = True
            page.update()

        # MENU FLUTUANTE (ESTILO SS RESTÔ) COM A NOVA GALERIA DE OBSERVAÇÕES
        def abrir_menu_flutuante(e):
            botoes_menu = []

            def acao(func):
                dlg_menu.open = False
                page.update()
                func()

            botoes_menu.append(
                ft.Container(
                    content=ft.Column([ft.Icon(ft.Icons.GRID_ON, size=35, color=ft.Colors.WHITE), ft.Text("Relatório", color=ft.Colors.WHITE, size=11, weight="bold")], alignment="center", horizontal_alignment="center"),
                    bgcolor=ft.Colors.BLUE_800, border_radius=12, ink=True, on_click=lambda _: acao(iniciar_relatorio)
                )
            )
            botoes_menu.append(
                ft.Container(
                    content=ft.Column([ft.Icon(ft.Icons.BAR_CHART, size=35, color=ft.Colors.WHITE), ft.Text("Painel", color=ft.Colors.WHITE, size=11, weight="bold")], alignment="center", horizontal_alignment="center"),
                    bgcolor=ft.Colors.TEAL_700, border_radius=12, ink=True, on_click=lambda _: acao(lambda: abrir_tela_dashboard(obra))
                )
            )
            
            # NOVO BOTÃO DA GALERIA
            botoes_menu.append(
                ft.Container(
                    content=ft.Column([ft.Icon(ft.Icons.PHOTO_CAMERA_BACK, size=35, color=ft.Colors.WHITE), ft.Text("Galeria\nObs.", color=ft.Colors.WHITE, size=11, weight="bold", text_align="center")], alignment="center", horizontal_alignment="center", spacing=2),
                    bgcolor=ft.Colors.INDIGO_600, border_radius=12, ink=True, on_click=lambda _: acao(lambda: abrir_tela_observacoes(obra))
                )
            )

            if perfil_user in ["admin", "editor"]:
                botoes_menu.append(
                    ft.Container(
                        content=ft.Column([ft.Icon(ft.Icons.CHECKLIST, size=35, color=ft.Colors.WHITE), ft.Text("Status\nRápido", color=ft.Colors.WHITE, size=11, weight="bold", text_align="center")], alignment="center", horizontal_alignment="center", spacing=2),
                        bgcolor=ft.Colors.ORANGE_700, border_radius=12, ink=True, on_click=lambda _: acao(lambda: abrir_tela_lancamento_status(obra))
                    )
                )
                botoes_menu.append(
                    ft.Container(
                        content=ft.Column([ft.Icon(ft.Icons.LIBRARY_ADD, size=35, color=ft.Colors.WHITE), ft.Text("+ Tarefa", color=ft.Colors.WHITE, size=11, weight="bold")], alignment="center", horizontal_alignment="center"),
                        bgcolor=ft.Colors.PURPLE_700, border_radius=12, ink=True, on_click=lambda _: acao(lambda: abrir_tela_lancamento_tarefas(obra))
                    )
                )
                botoes_menu.append(
                    ft.Container(
                        content=ft.Column([ft.Icon(ft.Icons.DELETE_SWEEP, size=35, color=ft.Colors.WHITE), ft.Text("- Tarefa", color=ft.Colors.WHITE, size=11, weight="bold")], alignment="center", horizontal_alignment="center"),
                        bgcolor=ft.Colors.RED_800, border_radius=12, ink=True, on_click=lambda _: acao(lambda: abrir_tela_remover_tarefas(obra))
                    )
                )

            grade = ft.GridView(
                controls=botoes_menu, runs_count=3, max_extent=100, child_aspect_ratio=1.0, spacing=15, run_spacing=15
            )

            dlg_menu = ft.AlertDialog(
                content=ft.Container(content=grade, width=320, padding=10),
                bgcolor=ft.Colors.TRANSPARENT, elevation=0, content_padding=0
            )
            page.overlay.append(dlg_menu)
            dlg_menu.open = True
            page.update()

        page.floating_action_button = ft.FloatingActionButton(
            icon=ft.Icons.APPS,
            bgcolor=ft.Colors.WHITE,
            on_click=abrir_menu_flutuante
        )

        lista_andares = ft.ListView(expand=True, spacing=10)

        def confirmar_exclusao_andar(andar_nome):
            def deletar(e):
                del banco_dados["obras"][obra][andar_nome]
                salvar_no_firebase(banco_dados) 
                dlg.open = False
                desenhar_lista_andares()
                page.update()
            dlg = ft.AlertDialog(title=ft.Text("Excluir Andar"), content=ft.Text(f"Deseja excluir o {andar_nome}º Pavimento?"), actions=[ft.TextButton("Cancelar", on_click=lambda e: fechar_dlg(dlg)), ft.TextButton("Excluir", on_click=deletar, style=ft.ButtonStyle(color=ft.Colors.RED))])
            page.overlay.append(dlg)
            dlg.open = True
            page.update()

        def fechar_dlg(dlg):
            dlg.open = False
            page.update()

        def desenhar_lista_andares():
            lista_andares.controls.clear()
            andares_ordenados = sorted(banco_dados["obras"][obra].keys(), key=lambda x: int(x) if str(x).isdigit() else 9999)
            for andar in andares_ordenados:
                botao_andar = ft.Container(
                    content=ft.Row([ft.Text(f"{andar}º Pavimento", size=18, weight=ft.FontWeight.W_600, color=ft.Colors.BLUE_900)], alignment=ft.MainAxisAlignment.CENTER), 
                    height=60, 
                    bgcolor=ft.Colors.GREY_100, 
                    border_radius=8, 
                    ink=True, 
                    on_click=lambda e, o=obra, a=andar: abrir_tela_apartamentos(o, a), 
                    on_long_press=(lambda e, a=andar: confirmar_exclusao_andar(a)) if perfil_user == "admin" else None
                )
                lista_andares.controls.append(botao_andar)
            page.update()

        def add_novo_andar(e):
            novo_andar = campo_novo_andar.value.strip().replace(".", "")
            if novo_andar and novo_andar not in banco_dados["obras"][obra]:
                banco_dados["obras"][obra][novo_andar] = {}
                salvar_no_firebase(banco_dados) 
                campo_novo_andar.value = ""
                desenhar_lista_andares()
                
        campo_novo_andar = ft.TextField(label="Novo Andar", expand=True, height=50, on_submit=add_novo_andar)
        
        linha_add = ft.Row([
            campo_novo_andar, 
            ft.IconButton(ft.Icons.ADD_CIRCLE, icon_color=ft.Colors.GREEN_600, icon_size=35, on_click=add_novo_andar),
            ft.Container(width=70) 
        ])
        linha_add.visible = (perfil_user in ["admin", "editor"])

        page.add(cabecalho, ft.Divider(color=ft.Colors.TRANSPARENT), lista_andares, linha_add)
        desenhar_lista_andares()


    def abrir_tela_historico():
        page.floating_action_button = None 
        page.controls.clear()
        page.vertical_alignment = ft.MainAxisAlignment.START
        
        cabecalho = ft.Row([
            ft.IconButton(icon=ft.Icons.ARROW_BACK, icon_color=ft.Colors.BLUE_700, on_click=lambda _: abrir_tela_obras()),
            ft.Text("Histórico de Ações", size=22, weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE_700)
        ])

        lista_hist = ft.ListView(expand=True, spacing=10)
        historico_dados = banco_dados.get("historico", [])

        if not historico_dados:
            lista_hist.controls.append(ft.Text("Nenhum registro encontrado.", color=ft.Colors.GREY_500))
        else:
            for item in historico_dados:
                try:
                    if isinstance(item, dict):
                        acao_str = str(item.get('acao', 'Ação'))
                        user_str = str(item.get('user', 'SISTEMA'))
                        data_str = str(item.get('data', ''))
                        detalhes_str = str(item.get('detalhes', ''))

                        cor_acao = ft.Colors.BLUE_700
                        if "Excluiu" in acao_str or "Removeu" in acao_str: cor_acao = ft.Colors.RED_700
                        elif "Criou" in acao_str or "Nova" in acao_str: cor_acao = ft.Colors.PURPLE_700
                        elif "Status" in acao_str or "Editou" in acao_str: cor_acao = ft.Colors.ORANGE_700

                        card = ft.Container(
                            content=ft.Column([
                                ft.Row([
                                    ft.Text(f"{data_str} - {user_str.upper()}", size=11, weight=ft.FontWeight.BOLD, color=ft.Colors.GREY_600),
                                    ft.Text(acao_str, size=11, weight=ft.FontWeight.BOLD, color=cor_acao),
                                ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                                ft.Text(detalhes_str, size=13, color=ft.Colors.BLACK87)
                            ]),
                            bgcolor=ft.Colors.GREY_100, padding=12, border_radius=8
                        )
                        lista_hist.controls.append(card)
                except Exception:
                    pass

        page.add(cabecalho, ft.Divider(color=ft.Colors.TRANSPARENT), lista_hist)
        page.update()

    def abrir_tela_usuarios():
        page.floating_action_button = None 
        page.controls.clear()
        page.vertical_alignment = ft.MainAxisAlignment.START
        
        cabecalho = ft.Row([
            ft.IconButton(icon=ft.Icons.ARROW_BACK, icon_color=ft.Colors.BLUE_700, on_click=lambda _: abrir_tela_obras()),
            ft.Text("Gestão de Usuários", size=22, weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE_700)
        ])

        lista_users = ft.ListView(expand=True, spacing=10)

        def abrir_popup_editar(username):
            info_atual = banco_dados["usuarios"][username]
            edit_nome = ft.TextField(label="Nome Completo", value=info_atual["nome"])
            edit_senha = ft.TextField(label="Nova Senha", value=info_atual["senha"])
            edit_perfil = ft.Dropdown(
                options=[ft.dropdown.Option("admin"), ft.dropdown.Option("editor"), ft.dropdown.Option("visualizador")],
                value=info_atual["perfil"]
            )

            def salvar_edicao(e):
                banco_dados["usuarios"][username]["nome"] = edit_nome.value.strip()
                banco_dados["usuarios"][username]["senha"] = edit_senha.value.strip()
                banco_dados["usuarios"][username]["perfil"] = edit_perfil.value
                salvar_no_firebase(banco_dados)
                dlg_edit.open = False
                desenhar_lista_usuarios()
                page.update()

            dlg_edit = ft.AlertDialog(
                title=ft.Text(f"Editar Usuário: {username}"),
                content=ft.Column([edit_nome, edit_senha, edit_perfil], tight=True),
                actions=[
                    ft.TextButton("Cancelar", on_click=lambda e: fechar_dlg(dlg_edit)),
                    ft.TextButton("Salvar", on_click=salvar_edicao, style=ft.ButtonStyle(color=ft.Colors.BLUE_700))
                ]
            )
            page.overlay.append(dlg_edit)
            dlg_edit.open = True
            page.update()

        def confirmar_exclusao_user(username):
            def deletar(e):
                if username == "admin":
                    snack = ft.SnackBar(ft.Text("Não é possível apagar o Administrador Principal!"), bgcolor=ft.Colors.RED_700)
                    page.overlay.append(snack)
                    snack.open = True
                else:
                    del banco_dados["usuarios"][username]
                    salvar_no_firebase(banco_dados)
                    desenhar_lista_usuarios()
                dlg.open = False
                page.update()
                
            dlg = ft.AlertDialog(title=ft.Text("Apagar Usuário"), content=ft.Text(f"Deseja remover o acesso de '{username}'?"), actions=[ft.TextButton("Cancelar", on_click=lambda e: fechar_dlg(dlg)), ft.TextButton("Excluir", on_click=deletar, style=ft.ButtonStyle(color=ft.Colors.RED))])
            page.overlay.append(dlg)
            dlg.open = True
            page.update()

        def fechar_dlg(dlg):
            dlg.open = False
            page.update()

        def desenhar_lista_usuarios():
            lista_users.controls.clear()
            for user, info in banco_dados["usuarios"].items():
                cor_icone = ft.Colors.RED if info["perfil"] == "admin" else (ft.Colors.BLUE if info["perfil"] == "editor" else ft.Colors.GREEN)
                icone = ft.Icons.ADMIN_PANEL_SETTINGS if info["perfil"] == "admin" else (ft.Icons.ENGINEERING if info["perfil"] == "editor" else ft.Icons.VISIBILITY)
                
                card_user = ft.Container(
                    content=ft.Row([
                        ft.Icon(icone, color=cor_icone),
                        ft.Column([
                            ft.Text(info["nome"], weight=ft.FontWeight.BOLD, size=15),
                            ft.Text(f"Login: {user} | Senha: {info['senha']}", size=11, color=ft.Colors.GREY_600)
                        ], expand=True),
                        ft.IconButton(ft.Icons.EDIT, icon_color=ft.Colors.BLUE_500, tooltip="Editar", on_click=lambda e, u=user: abrir_popup_editar(u)),
                        ft.IconButton(ft.Icons.DELETE, icon_color=ft.Colors.RED_300, tooltip="Apagar", on_click=lambda e, u=user: confirmar_exclusao_user(u))
                    ]),
                    bgcolor=ft.Colors.GREY_100, padding=10, border_radius=8
                )
                lista_users.controls.append(card_user)
            page.update()

        campo_novo_login = ft.TextField(label="Novo Login (ex: joao)", expand=True)
        campo_novo_nome = ft.TextField(label="Nome Completo", expand=True)
        campo_nova_senha = ft.TextField(label="Senha", expand=True)
        dropdown_perfil = ft.Dropdown(
            options=[ft.dropdown.Option("admin"), ft.dropdown.Option("editor"), ft.dropdown.Option("visualizador")],
            value="visualizador", width=120
        )

        def add_novo_usuario(e):
            user = campo_novo_login.value.strip().replace(".", "").lower()
            if user and user not in banco_dados["usuarios"]:
                banco_dados["usuarios"][user] = {
                    "senha": campo_nova_senha.value.strip(),
                    "nome": campo_novo_nome.value.strip() or user,
                    "perfil": dropdown_perfil.value
                }
                salvar_no_firebase(banco_dados)
                campo_novo_login.value = ""
                campo_novo_nome.value = ""
                campo_nova_senha.value = ""
                desenhar_lista_usuarios()

        area_cadastro = ft.Column([
            ft.Text("Cadastrar Novo Acesso", weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE_800),
            campo_novo_nome,
            ft.Row([campo_novo_login, campo_nova_senha]),
            ft.Row([dropdown_perfil, ft.ElevatedButton("Gravar", on_click=add_novo_usuario, bgcolor=ft.Colors.GREEN_600, color=ft.Colors.WHITE, expand=True)], alignment=ft.MainAxisAlignment.SPACE_BETWEEN)
        ], spacing=10)

        page.add(cabecalho, lista_users, ft.Divider(), area_cadastro)
        desenhar_lista_usuarios()

    # ==========================================
    # TELA 1: CADASTRO E SELEÇÃO DE OBRAS
    # ==========================================
    def abrir_tela_obras():
        page.floating_action_button = None 
        page.controls.clear()
        page.vertical_alignment = ft.MainAxisAlignment.START
        
        perfil_user = ACTIVE_SESSIONS.get(page.session_id, {}).get("perfil")
        nome_user = ACTIVE_SESSIONS.get(page.session_id, {}).get("nome")

        def fazer_logout(e):
            if page.session_id in ACTIVE_SESSIONS:
                del ACTIVE_SESSIONS[page.session_id]
            try:
                page.client_storage.remove("usuario")
                page.client_storage.remove("perfil")
                page.client_storage.remove("nome")
            except: pass
            abrir_tela_login()

        cabecalho_obras = ft.Row([
            ft.Column([
                ft.Text(f"Olá, {nome_user}", size=14, color=ft.Colors.GREY_600),
                ft.Text("Minhas Obras", size=26, weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE_800)
            ], expand=True),
            ft.IconButton(icon=ft.Icons.HISTORY, icon_color=ft.Colors.BLUE_700, icon_size=28, on_click=lambda _: abrir_tela_historico(), visible=(perfil_user in ["admin", "editor"])),
            ft.IconButton(icon=ft.Icons.MANAGE_ACCOUNTS, icon_color=ft.Colors.BLUE_700, icon_size=28, on_click=lambda _: abrir_tela_usuarios(), visible=(perfil_user == "admin")),
            ft.IconButton(icon=ft.Icons.LOGOUT, icon_color=ft.Colors.RED_600, icon_size=28, on_click=fazer_logout, tooltip="Sair da Conta")
        ])

        lista_obras = ft.ListView(expand=True, spacing=15)

        def confirmar_exclusao_obra(obra_nome):
            def deletar(e):
                del banco_dados["obras"][obra_nome]
                salvar_no_firebase(banco_dados) 
                dlg.open = False
                desenhar_lista_obras()
                page.update()
            dlg = ft.AlertDialog(title=ft.Text("Excluir Obra"), content=ft.Text(f"ATENÇÃO: Excluir a obra '{obra_nome}'?"), actions=[ft.TextButton("Cancelar", on_click=lambda e: fechar_dlg(dlg)), ft.TextButton("Excluir", on_click=deletar, style=ft.ButtonStyle(color=ft.Colors.RED))])
            page.overlay.append(dlg)
            dlg.open = True
            page.update()

        def fechar_dlg(dlg):
            dlg.open = False
            page.update()

        def desenhar_lista_obras():
            lista_obras.controls.clear()
            for obra in sorted(banco_dados["obras"].keys()):
                botao_obra = ft.Container(
                    content=ft.Row([ft.Icon(ft.Icons.DOMAIN, color=ft.Colors.WHITE, size=28), ft.Text(obra, size=20, weight=ft.FontWeight.BOLD, color=ft.Colors.WHITE)], alignment=ft.MainAxisAlignment.CENTER, spacing=15), 
                    height=80, 
                    bgcolor=ft.Colors.BLUE_600, 
                    border_radius=12, 
                    ink=True, 
                    on_click=lambda e, o=obra: abrir_tela_andares(o), 
                    on_long_press=(lambda e, o=obra: confirmar_exclusao_obra(o)) if perfil_user == "admin" else None
                )
                lista_obras.controls.append(botao_obra)
            page.update()

        def add_nova_obra(e):
            nova_obra = campo_nova_obra.value.strip().replace(".", "")
            if nova_obra and nova_obra not in banco_dados["obras"]:
                banco_dados["obras"][nova_obra] = {}
                salvar_no_firebase(banco_dados) 
                campo_nova_obra.value = ""
                desenhar_lista_obras()
                
        campo_nova_obra = ft.TextField(label="Cadastrar Nova Obra", expand=True, height=50, on_submit=add_nova_obra)
        
        linha_add = ft.Row([campo_nova_obra, ft.IconButton(ft.Icons.ADD_CIRCLE, icon_color=ft.Colors.GREEN_600, icon_size=35, on_click=add_nova_obra)])
        linha_add.visible = (perfil_user == "admin") 

        page.add(cabecalho_obras, ft.Divider(color=ft.Colors.TRANSPARENT), lista_obras, linha_add)
        desenhar_lista_obras()

    # ==========================================
    # TELA 0: O PORTÃO DE ENTRADA (LOGIN)
    # ==========================================
    def abrir_tela_login():
        page.floating_action_button = None 
        page.controls.clear()
        page.vertical_alignment = ft.MainAxisAlignment.CENTER
        page.horizontal_alignment = ft.CrossAxisAlignment.CENTER
        
        campo_usuario = ft.TextField(label="Usuário (Login)", prefix_icon=ft.Icons.PERSON)
        chk_manter = ft.Checkbox(label="Manter-me logado", value=True)
        
        def validar_login(e):
            usr = campo_usuario.value.strip().lower()
            pwd = campo_senha.value.strip()
            
            if usr in banco_dados["usuarios"] and banco_dados["usuarios"][usr]["senha"] == pwd:
                dados_usr = banco_dados["usuarios"][usr]
                
                ACTIVE_SESSIONS[page.session_id] = {
                    "usuario": usr, "perfil": dados_usr["perfil"], "nome": dados_usr["nome"]
                }
                
                if chk_manter.value:
                    try:
                        page.client_storage.set("usuario", usr)
                        page.client_storage.set("perfil", dados_usr["perfil"])
                        page.client_storage.set("nome", dados_usr["nome"])
                    except: pass
                
                abrir_tela_obras()
            else:
                snack = ft.SnackBar(ft.Text("Acesso Negado: Usuário ou senha incorretos!"), bgcolor=ft.Colors.RED_700)
                page.overlay.append(snack)
                snack.open = True
                page.update()

        campo_senha = ft.TextField(label="Senha", prefix_icon=ft.Icons.LOCK, password=True, can_reveal_password=True, on_submit=validar_login)
        btn_entrar = ft.ElevatedButton("ENTRAR NO SISTEMA", on_click=validar_login, width=250, height=50, style=ft.ButtonStyle(bgcolor=ft.Colors.BLUE_700, color=ft.Colors.WHITE))
        
        caixa_login = ft.Container(
            content=ft.Column([
                ft.Icon(ft.Icons.APARTMENT, size=60, color=ft.Colors.BLUE_700),
                ft.Text("App Vistoria", size=24, weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE_900),
                ft.Divider(color=ft.Colors.TRANSPARENT),
                campo_usuario,
                campo_senha,
                chk_manter,
                btn_entrar
            ], horizontal_alignment=ft.CrossAxisAlignment.CENTER),
            padding=30, border_radius=15, bgcolor=ft.Colors.GREY_100, width=320
        )
        page.add(caixa_login)

    # ==========================================
    # LÓGICA DE ARRANQUE (MANTER LOGADO)
    # ==========================================
    try:
        if page.client_storage.contains_key("usuario"):
            salvo_usr = page.client_storage.get("usuario")
            salvo_perfil = page.client_storage.get("perfil")
            salvo_nome = page.client_storage.get("nome")
            
            ACTIVE_SESSIONS[page.session_id] = {
                "usuario": salvo_usr, "perfil": salvo_perfil, "nome": salvo_nome
            }
            abrir_tela_obras()
        else:
            abrir_tela_login()
    except Exception:
        abrir_tela_login()

os.makedirs("assets", exist_ok=True)
porta = int(os.environ.get("PORT", 8000))

if __name__ == "__main__":
    # upload_dir ativado para permitir armazenamento das fotos!
    ft.run(main, port=porta, host="0.0.0.0", assets_dir="assets", upload_dir="assets")
