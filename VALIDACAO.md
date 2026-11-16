# Validação — Monitor Global 1.1.0

Ambiente: Ubuntu, GNOME Shell 50.1, GTK 4.22, libadwaita 1.9 e Python local.
Validado em 16/11/2026.

- **41 testes de modelo/serviço**, sem consultas externas: defaults, validação,
  limites, permissões, cache, retenção de histórico, orçamento diário, backup sem
  credenciais, importação atômica, parser das fontes, consultas agrupadas de câmbio,
  recorrência diária/semanal/mensal, ano bissexto, horário de verão, antecedência,
  retomada após atraso, concorrência, idempotência, tentativas, adiar e adaptadores.
- **48 verificações de interface GTK**: nove páginas, cadastro de ativo,
  fixação na barra, criação/edição de lembrete, alerta, destino inicialmente
  desativado, temas e personalização, perfil, restauração sem perder agenda,
  notificação aceita pelo GNOME, ação de adiar e serviço após fechar a janela.
- **17 verificações de integração GNOME Shell**: carregamento do ZIP, D-Bus,
  estado inicial, indicadores, abertura e instância única da janela, fechamento,
  serviço persistente, desativação/reativação e execução das suítes GTK/HTTP.
- **3 testes HTTP reais**, exclusivamente contra servidor local temporário:
  JSON personalizado, autenticação e idempotência, preservação literal do conteúdo,
  resposta 202, erro HTTP e redirecionamento bloqueado sem repassar credenciais.

Total: **109 testes/verificações**.

Inclui persistência/validação da posição da barra; movimentação à esquerda, à
direita e antes do relógio no GNOME; busca de cidade com filtro de país, estado,
acentos e municípios homônimos; escolha explícita; descarte de respostas antigas;
falhas de rede; preservação da cidade existente; localização opcional somente após
clique, cancelamento ao fechar e encerramento da captura após uma única posição.
Os casos de autorização, recusa e captura do GeoClue são simulados no processo de
teste; não foi concedida permissão de localização na sessão principal.

Consultas públicas reais de cidades confirmadas: Campinas/SP no Brasil,
São José no Brasil e Porto em Portugal. A prévia do formulário foi conferida em
`/tmp/monitor-global-city-preview.png`.

As suítes gráficas usam D-Bus e dados XDG isolados; não alteram agenda ou
preferências da sessão principal. Fixtures de interface existem somente no
processo de teste. Nenhum teste envia mensagens a números de WhatsApp, e-mails
ou destinos externos. O adaptador WhatsApp foi validado pela estrutura da
requisição; uma entrega real depende de conta, token, número e template do usuário.

As consultas públicas reais de AwesomeAPI, Coinbase Exchange, brapi e Open-Meteo
responderam com sucesso. A captura `/tmp/monitor-global-preview.png` foi
inspecionada: visual escuro/verde, ícones simbólicos com cores por módulo,
cartões e gráficos reais, barra com múltiplos indicadores e tabela de navegação.

O ZIP é conferido contra as fontes e inclui todos os módulos e ícones, sem
`__pycache__`. Sintaxe Python, Bash e carregamento JavaScript no GNOME verificados.
O instalador preserva as outras extensões, configura o atalho e o início do
serviço com a sessão. O novo código da barra pode exigir sair e entrar na sessão.

Limites explícitos: dados gratuitos têm atrasos/cotas; cobertura internacional de
bolsa requer chave gratuita Alpha Vantage; Open-Meteo gratuito para uso não
comercial; computador desligado não executa os agendamentos locais; WhatsApp
oficial pode cobrar. Histórico de mercado não disponível usa apenas amostras
locais reais, sem simular preços. Aceitação HTTP não comprova leitura/entrega ao
destinatário; o receptor precisa honrar `Idempotency-Key` para deduplicar também
em cenários de timeout depois de aceitar uma requisição.
