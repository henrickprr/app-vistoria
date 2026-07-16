import flet as ft
import json
import urllib.request
import os
from fpdf import FPDF 
# CORREÇÃO CRÍTICA: Importando os seletores de posição oficiais do FPDF2
from fpdf.enums import XPos, YPos

FIREBASE_URL = "https://app-vistoria-986c3-default-rtdb.firebaseio.com/banco_dados.json"

def main(page: ft.Page):
    page.title = "App de Vistoria"
    page.theme_mode = ft.ThemeMode.LIGHT
    page.padding = 20
    page.window.width = 420  
    page.window.height = 750

    lista_servicos_base = [
        "Revestimento piso banheiro", "Dreno", "Revestimento porcelanato", 
        "Limpeza", "Forro de gesso", "Forro da varanda", 
        "Ralos da varanda", "Forro do banheiro"
    ]

    # ==========================================
    # FUNÇÕES DE SINCRONIZAÇÃO COM A NUVEM
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

    def salvar_no_firebase(dados):
        try:
            req = urllib.request.Request(FIREBASE_URL, data=json.dumps(dados).encode('utf-8'), method='PUT')
            req.add_header('Content-Type', 'application/json')
            urllib.request.urlopen(req)
            
            snack = ft.SnackBar(ft.Text("☁️ Sincronizado com o Firebase!", color=ft.Colors.WHITE), bgcolor=ft.Colors.GREEN_700)
            page.overlay.append(snack)
            snack.open = True
            page.update()
        except Exception as e:
            pass

    banco_dados = carregar_do_firebase()

    if banco_dados is None:
        banco_dados = {"Golden Flat": {}, "Residência Alvorada": {}}

    if "Golden Flat" in banco_dados:
        precisa_salvar = False
        for andar in range(1, 18):
            andar_str = str(andar)
            if andar_str not in banco_dados["Golden Flat"]:
                banco_dados["Golden Flat"][andar_str] = {}
                precisa_salvar = True
            
            locais_iniciais = [f"{andar}{apto:02d}" for apto in range(1, 15)] + ["Corredor"]
            
            for local in locais_iniciais:
                if local not in banco_dados["Golden Flat"][andar_str]:
                    banco_dados["Golden Flat"][andar_str][local] = {
                        s: {"status": "Não Iniciado", "obs": ""} for s in lista_servicos_base
                    }
                    precisa_salvar = True
        if precisa_salvar: salvar_no_firebase(banco_dados)

    def get_cor_status(status):
        if status == "Finalizado": return ft.Colors.GREEN_500
        if status == "Não Conforme": return ft.Colors.RED_500
        if status == "Em Andamento": return ft.Colors.BLUE_500
        return ft.Colors.GREY_400


    # ==========================================
    # FUNÇÃO DE GERAÇÃO DO ARQUIVO PDF (CORRIGIDA)
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

        # Legenda corrigida com Enums oficiais do FPDF2
        pdf.set_font("helvetica", 'B', 9)
        pdf.set_fill_color(76, 175, 80); pdf.cell(8, 5, "", border=1, fill=True, new_x=XPos.RIGHT, new_y=YPos.TOP); pdf.cell(16, 5, " OK", new_x=XPos.RIGHT, new_y=YPos.TOP)
        pdf.set_fill_color(244, 67, 54); pdf.cell(8, 5, "", border=1, fill=True, new_x=XPos.RIGHT, new_y=YPos.TOP); pdf.cell(22, 5, " Pendente", new_x=XPos.RIGHT, new_y=YPos.TOP)
        pdf.set_fill_color(33, 150, 243); pdf.cell(8, 5, "", border=1, fill=True, new_x=XPos.RIGHT, new_y=YPos.TOP); pdf.cell(24, 5, " Em Andam.", new_x=XPos.RIGHT, new_y=YPos.TOP)
        pdf.set_fill_color(189, 189, 189); pdf.cell(8, 5, "", border=1, fill=True, new_x=XPos.RIGHT, new_y=YPos.TOP); pdf.cell(30, 5, " Não Iniciado", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
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
                if local in banco_dados[obra][andar] and servico_escolhido in banco_dados[obra][andar][local]:
                    status = banco_dados[obra][andar][local][servico_escolhido]["status"]

                if status == "Finalizado": pdf.set_fill_color(76, 175, 80)
                elif status == "Não Conforme": pdf.set_fill_color(244, 67, 54)
                elif status == "Em Andamento": pdf.set_fill_color(33, 150, 243)
                else: pdf.set_fill_color(189, 189, 189)

                larg = largura_corr if local == "Corredor" else largura_apto
                
                if i == len(locais) - 1:
                    pdf.cell(larg, altura_celula, "", border=1, fill=True, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
                else:
                    pdf.cell(larg, altura_celula, "", border=1, fill=True, new_x=XPos.RIGHT, new_y=YPos.TOP)

        pdf.output(caminho_arquivo)


    # ==========================================
    # TELA 5: RELATÓRIO MATRICIAL (App Web)
    # ==========================================
    def abrir_tela_relatorio(obra, servico_escolhido):
        page.controls.clear()

        cabecalho = ft.Row([
            ft.IconButton(icon=ft.Icons.ARROW_BACK, icon_color=ft.Colors.BLUE_700, on_click=lambda _: abrir_tela_andares(obra)),
            ft.Text(f"Relatório: {servico_escolhido}", size=18, weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE_700, expand=True)
        ])

        andares_ordenados = sorted(banco_dados[obra].keys(), key=lambda x: int(x) if str(x).isdigit() else 9999)

        def acionar_pdf(e):
            try:
                if not os.path.exists("assets"):
                    os.makedirs("assets")
                    
                nome_pdf = f"Relatorio_{obra.replace(' ', '_')}_{servico_escolhido.replace(' ', '_')}.pdf"
                caminho_completo = os.path.join("assets", nome_pdf)

                gerar_pdf(obra, servico_escolhido, andares_ordenados, caminho_completo)
                
                def fechar_dlg_download(e):
                    dlg_download.open = False
                    page.update()

                dlg_download = ft.AlertDialog(
                    title=ft.Text("✅ Relatório Pronto!", size=20, weight=ft.FontWeight.BOLD, color=ft.Colors.GREEN_700),
                    content=ft.Text("Seu arquivo PDF foi gerado e está pronto para impressão. Clique no botão abaixo para abrir/salvar."),
                    actions=[
                        ft.ElevatedButton(
                            text="📥 Baixar PDF", 
                            url=f"/{nome_pdf}", 
                            url_target="_blank", 
                            bgcolor=ft.Colors.GREEN_700, 
                            color=ft.Colors.WHITE,
                            on_click=fechar_dlg_download
                        )
                    ],
                    actions_alignment=ft.MainAxisAlignment.CENTER
                )

                page.overlay.append(dlg_download)
                dlg_download.open = True
                page.update()
                
            except Exception as ex:
                snack_erro = ft.SnackBar(ft.Text(f"Erro ao gerar PDF: {ex}"), bgcolor=ft.Colors.RED_700)
                page.overlay.append(snack_erro)
                snack_erro.open = True
                page.update()

        botao_exportar = ft.Container(
            content=ft.Row([ft.Icon(ft.Icons.PICTURE_AS_PDF, color=ft.Colors.WHITE), ft.Text("Gerar PDF (A4)", weight=ft.FontWeight.BOLD, color=ft.Colors.WHITE)], alignment=ft.MainAxisAlignment.CENTER),
            bgcolor=ft.Colors.RED_700, padding=12, border_radius=8, ink=True, on_click=acionar_pdf
        )

        legenda = ft.Row([
            ft.Container(width=15, height=15, bgcolor=ft.Colors.GREEN_500, border_radius=3), ft.Text("OK", size=12),
            ft.Container(width=15, height=15, bgcolor=ft.Colors.RED_500, border_radius=3), ft.Text("Pend.", size=12),
            ft.Container(width=15, height=15, bgcolor=ft.Colors.BLUE_500, border_radius=3), ft.Text("Andam.", size=12),
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
                if local_str in banco_dados[obra][andar] and servico_escolhido in banco_dados[obra][andar][local_str]:
                    status_atual = banco_dados[obra][andar][local_str][servico_escolhido]["status"]
                
                cor_quadrado = get_cor_status(status_atual)
                largura_celula = 45 if local_str == "Corredor" else 35
                
                quadrado = ft.Container(width=largura_celula, height=35, bgcolor=cor_quadrado, border_radius=4, tooltip=f"{local_str}: {status_atual}")
                linha_andar.controls.append(quadrado)
                
            tabela.controls.append(linha_andar)

        area_rolagem = ft.Row([ft.Column([tabela], scroll=ft.ScrollMode.AUTO)], scroll=ft.ScrollMode.AUTO, expand=True)

        page.add(cabecalho, botao_exportar, legenda, ft.Divider(height=10, color=ft.Colors.TRANSPARENT), area_rolagem)
        page.update()


    # ==========================================
    # TELA 4: ATIVIDADES
    # ==========================================
    def abrir_tela_atividades(obra, andar, apto):
        page.controls.clear()
        nome_tela = apto if apto == "Corredor" else f"Apto {apto}"

        cabecalho = ft.Row([
            ft.IconButton(icon=ft.Icons.ARROW_BACK, icon_color=ft.Colors.BLUE_700, on_click=lambda _: abrir_tela_apartamentos(obra, andar)),
            ft.Text(nome_tela, size=22, weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE_700)
        ])

        container_botoes = ft.Row(wrap=True, spacing=10, run_spacing=10)
        area_rolagem = ft.Column([container_botoes], scroll=ft.ScrollMode.AUTO, expand=True)

        def confirmar_exclusao_ativ(nome_servico):
            def deletar(e):
                del banco_dados[obra][andar][apto][nome_servico]
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
            for nome_servico, dados in list(banco_dados[obra][andar][apto].items()):
                cor_botao = get_cor_status(dados["status"])
                botao_atividade = ft.Container(
                    content=ft.Text(nome_servico, size=14, weight=ft.FontWeight.W_600, color=ft.Colors.WHITE),
                    bgcolor=cor_botao, padding=12, border_radius=20, ink=True,
                    on_click=lambda e, s=nome_servico: abrir_popup_status(s), on_long_press=lambda e, s=nome_servico: confirmar_exclusao_ativ(s) 
                )
                container_botoes.controls.append(botao_atividade)
            page.update()

        def abrir_popup_status(nome_servico):
            dados_atuais = banco_dados[obra][andar][apto][nome_servico]
            menu_dropdown = ft.Dropdown(options=[ft.dropdown.Option("Não Iniciado"), ft.dropdown.Option("Em Andamento"), ft.dropdown.Option("Finalizado"), ft.dropdown.Option("Não Conforme")], value=dados_atuais["status"], width=250)
            campo_obs = ft.TextField(label="Observação", value=dados_atuais["obs"], multiline=True, visible=(dados_atuais["status"] == "Não Conforme"))
            
            def ao_mudar_dropdown(e):
                campo_obs.visible = (menu_dropdown.value == "Não Conforme")
                page.update()
            menu_dropdown.on_change = ao_mudar_dropdown

            def salvar_popup(e):
                banco_dados[obra][andar][apto][nome_servico]["status"] = menu_dropdown.value
                banco_dados[obra][andar][apto][nome_servico]["obs"] = campo_obs.value
                salvar_no_firebase(banco_dados) 
                janela_popup.open = False
                desenhar_botoes_atividades()
                page.update()

            janela_popup = ft.AlertDialog(title=ft.Text(f"{nome_servico}"), content=ft.Column([menu_dropdown, campo_obs], tight=True), actions=[ft.Container(content=ft.Text("Salvar", color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD), bgcolor=ft.Colors.BLUE_700, padding=10, border_radius=8, ink=True, on_click=salvar_popup)])
            page.overlay.append(janela_popup)
            janela_popup.open = True
            page.update()

        campo_nova = ft.TextField(label="Nova Atividade", expand=True, height=50)
        def add_nova_atividade(e):
            nova_ativ = campo_nova.value.strip().replace(".", "") 
            if nova_ativ and nova_ativ not in banco_dados[obra][andar][apto]:
                banco_dados[obra][andar][apto][nova_ativ] = {"status": "Não Iniciado", "obs": ""}
                salvar_no_firebase(banco_dados) 
                campo_nova.value = ""
                desenhar_botoes_atividades()
        linha_add = ft.Row([campo_nova, ft.IconButton(ft.Icons.ADD_CIRCLE, icon_color=ft.Colors.GREEN_600, icon_size=35, on_click=add_nova_atividade)])
        
        desenhar_botoes_atividades()
        page.add(cabecalho, area_rolagem, ft.Divider(), linha_add)


    # ==========================================
    # TELA 3: APARTAMENTOS E CORREDOR
    # ==========================================
    def abrir_tela_apartamentos(obra, andar):
        page.controls.clear()
        cabecalho = ft.Row([ft.IconButton(icon=ft.Icons.ARROW_BACK, icon_color=ft.Colors.BLUE_700, on_click=lambda _: abrir_tela_andares(obra)), ft.Text(f"{andar}º Pavimento", size=22, weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE_700)])
        grid_aptos = ft.GridView(expand=True, runs_count=3, max_extent=110, child_aspect_ratio=1.0, spacing=15, run_spacing=15)

        def confirmar_exclusao_apto(apto_nome):
            def deletar(e):
                del banco_dados[obra][andar][apto_nome]
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
            aptos_ordenados = sorted(banco_dados[obra][andar].items(), key=lambda x: int(x[0]) if str(x[0]).isdigit() else 9999)
            
            for numero_apto, atividades in aptos_ordenados:
                cor_fundo = ft.Colors.GREY_400 
                if atividades: 
                    status_das_atividades = [dados["status"] for dados in atividades.values()]
                    if "Não Conforme" in status_das_atividades: cor_fundo = ft.Colors.RED_500 
                    elif all(status == "Finalizado" for status in status_das_atividades): cor_fundo = ft.Colors.GREEN_500 
                    elif any(status != "Não Iniciado" for status in status_das_atividades): cor_fundo = ft.Colors.BLUE_500 
                
                tamanho_fonte = 18 if numero_apto == "Corredor" else 26
                
                bloco = ft.Container(content=ft.Column([ft.Text(value=numero_apto, size=tamanho_fonte, weight=ft.FontWeight.W_500, color=ft.Colors.WHITE), ft.Icon(ft.Icons.APPS, color=ft.Colors.WHITE70, size=24)], alignment=ft.MainAxisAlignment.CENTER, horizontal_alignment=ft.CrossAxisAlignment.CENTER), bgcolor=cor_fundo, border_radius=10, ink=True, on_click=lambda e, o=obra, a=andar, apt=numero_apto: abrir_tela_atividades(o, a, apt), on_long_press=lambda e, apt=numero_apto: confirmar_exclusao_apto(apt))
                grid_aptos.controls.append(bloco)
            page.update()

        campo_novo_apto = ft.TextField(label="Novo Apto/Local", expand=True, height=50)
        def add_novo_apto(e):
            novo_apto = campo_novo_apto.value.strip().replace(".", "")
            if novo_apto and novo_apto not in banco_dados[obra][andar]:
                banco_dados[obra][andar][novo_apto] = {s: {"status": "Não Iniciado", "obs": ""} for s in lista_servicos_base}
                salvar_no_firebase(banco_dados) 
                campo_novo_apto.value = ""
                desenhar_grid()
        linha_add = ft.Row([campo_novo_apto, ft.IconButton(ft.Icons.ADD_CIRCLE, icon_color=ft.Colors.GREEN_600, icon_size=35, on_click=add_novo_apto)])
        
        desenhar_grid()
        page.add(cabecalho, grid_aptos, ft.Divider(), linha_add)


    # ==========================================
    # TELA 2: ANDARES E GERADOR DE RELATÓRIO
    # ==========================================
    def abrir_tela_andares(obra):
        page.controls.clear()
        
        cabecalho = ft.Row([
            ft.IconButton(icon=ft.Icons.ARROW_BACK, icon_color=ft.Colors.BLUE_700, on_click=lambda _: abrir_tela_obras()),
            ft.Text(f"{obra}", size=22, weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE_700, expand=True)
        ])
        
        def iniciar_relatorio(e):
            servicos_disponiveis = set(lista_servicos_base)
            for andar_dados in banco_dados[obra].values():
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
                actions=[ft.TextButton("Cancelar", on_click=lambda e: fechar_dlg(dlg_rel)), ft.TextButton("Gerar Visão", on_click=gerar, style=ft.ButtonStyle(color=ft.Colors.BLUE_700))]
            )
            page.overlay.append(dlg_rel)
            dlg_rel.open = True
            page.update()

        botao_relatorio = ft.Container(
            content=ft.Row([ft.Icon(ft.Icons.GRID_ON, color=ft.Colors.WHITE), ft.Text("Gerar Relatório", weight=ft.FontWeight.BOLD, color=ft.Colors.WHITE)], alignment=ft.MainAxisAlignment.CENTER),
            bgcolor=ft.Colors.BLUE_800, padding=12, border_radius=8, ink=True, on_click=iniciar_relatorio
        )

        lista_andares = ft.ListView(expand=True, spacing=10)

        def confirmar_exclusao_andar(andar_nome):
            def deletar(e):
                del banco_dados[obra][andar_nome]
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
            andares_ordenados = sorted(banco_dados[obra].keys(), key=lambda x: int(x) if str(x).isdigit() else 9999)
            for andar in andares_ordenados:
                botao_andar = ft.Container(content=ft.Row([ft.Text(f"{andar}º Pavimento", size=18, weight=ft.FontWeight.W_600, color=ft.Colors.BLUE_900)], alignment=ft.MainAxisAlignment.CENTER), height=60, bgcolor=ft.Colors.GREY_100, border_radius=8, ink=True, on_click=lambda e, o=obra, a=andar: abrir_tela_apartamentos(o, a), on_long_press=lambda e, a=andar: confirmar_exclusao_andar(a))
                lista_andares.controls.append(botao_andar)
            page.update()

        campo_novo_andar = ft.TextField(label="Novo Andar", expand=True, height=50)
        def add_novo_andar(e):
            novo_andar = campo_novo_andar.value.strip().replace(".", "")
            if novo_andar and novo_andar not in banco_dados[obra]:
                banco_dados[obra][novo_andar] = {}
                salvar_no_firebase(banco_dados) 
                campo_novo_andar.value = ""
                desenhar_lista_andares()
        linha_add = ft.Row([campo_novo_andar, ft.IconButton(ft.Icons.ADD_CIRCLE, icon_color=ft.Colors.GREEN_600, icon_size=35, on_click=add_novo_andar)])

        desenhar_lista_andares()
        page.add(cabecalho, botao_relatorio, ft.Divider(color=ft.Colors.TRANSPARENT), lista_andares, linha_add)


    # ==========================================
    # TELA 1: OBRAS
    # ==========================================
    def abrir_tela_obras():
        page.controls.clear()
        titulo = ft.Text("Minhas Obras", size=26, weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE_800)
        lista_obras = ft.ListView(expand=True, spacing=15)

        def confirmar_exclusao_obra(obra_nome):
            def deletar(e):
                del banco_dados[obra_nome]
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
            for obra in sorted(banco_dados.keys()):
                botao_obra = ft.Container(content=ft.Row([ft.Icon(ft.Icons.DOMAIN, color=ft.Colors.WHITE, size=28), ft.Text(obra, size=20, weight=ft.FontWeight.BOLD, color=ft.Colors.WHITE)], alignment=ft.MainAxisAlignment.CENTER, spacing=15), height=80, bgcolor=ft.Colors.BLUE_600, border_radius=12, ink=True, on_click=lambda e, o=obra: abrir_tela_andares(o), on_long_press=lambda e, o=obra: confirmar_exclusao_obra(o))
                lista_obras.controls.append(botao_obra)
            page.update()

        campo_nova_obra = ft.TextField(label="Cadastrar Nova Obra", expand=True, height=50)
        def add_nova_obra(e):
            nova_obra = campo_nova_obra.value.strip().replace(".", "")
            if nova_obra and nova_obra not in banco_dados:
                banco_dados[nova_obra] = {}
                salvar_no_firebase(banco_dados) 
                campo_nova_obra.value = ""
                desenhar_lista_obras()
        linha_add = ft.Row([campo_nova_obra, ft.IconButton(ft.Icons.ADD_CIRCLE, icon_color=ft.Colors.GREEN_600, icon_size=35, on_click=add_nova_obra)])

        desenhar_lista_obras()
        page.add(titulo, ft.Divider(color=ft.Colors.TRANSPARENT), lista_obras, linha_add)

    abrir_tela_obras()


os.makedirs("assets", exist_ok=True)
porta = int(os.environ.get("PORT", 8000))

if __name__ == "__main__":
    ft.app(target=main, view=ft.AppView.WEB_BROWSER, port=porta, host="0.0.0.0", assets_dir="assets")
