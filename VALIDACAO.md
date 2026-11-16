# Validação — Monitor Global 1.3.1

Atualização visual 1.3.2: novo ícone PNG com alfa, 1254 × 1254, criado pela
ferramenta image_gen integrada a partir das cores da referência. Conferido no
tamanho real da janela em sessão GNOME isolada; ícone aplicado à marca da janela,
ao atalho e às notificações. O pacote foi conferido contra as fontes e o pipeline
de empacotamento passou nos 69 testes de modelo/histórico e na sintaxe Python.
Os resultados completos de interface abaixo correspondem à versão funcional 1.3.1.

Ambiente: Ubuntu, GNOME Shell 50.1, GTK 4.22, libadwaita 1.9 e Python local.
Validado em 16/11/2026.

- **69 testes de modelo/serviço e histórico**, sem consultas externas: defaults, validação,
  limites, permissões, cache, retenção de histórico, orçamento diário, backup sem
  credenciais, importação atômica, parser das fontes, consultas agrupadas de câmbio,
  recorrência diária/semanal/mensal, ano bissexto, horário de verão, antecedência,
  retomada após atraso, concorrência, idempotência, tentativas, adiar e adaptadores.
- **85 verificações de interface GTK**: nove páginas, cadastro de ativo,
  fixação na barra, criação/edição de lembrete, alerta, destino inicialmente
  desativado, temas e personalização, perfil, restauração sem perder agenda,
  notificação aceita pelo GNOME, ação de adiar e serviço após fechar a janela.
- **27 verificações de integração GNOME Shell**: carregamento do ZIP, D-Bus,
  estado inicial, indicadores, abertura e instância única da janela, fechamento,
  serviço persistente, desativação/reativação e execução das suítes GTK/HTTP.
- **3 testes HTTP reais**, exclusivamente contra servidor local temporário:
  JSON personalizado, autenticação e idempotência, preservação literal do conteúdo,
  resposta 202, erro HTTP e redirecionamento bloqueado sem repassar credenciais.

Total: **184 testes/verificações**.

Versão 1.3.1: rótulo do clima com 1 a 3 letras; iniciais VDC/SP e abreviação SAL;
campo limitado a três caracteres e validação de letras no modelo; migração dos
rótulos longos preservando coordenadas, identidades, dados meteorológicos e
configurações; siglas curtas personalizadas preservadas. O nome completo continua
na janela e no tooltip. Códigos financeiros mantêm seu formato original.

Versão 1.3.0: sete abas da agenda com contadores e busca integrada; atrasados
separados de futuros, concluídos e pausados; vencimento após aviso antecipado;
fronteira exata do horário; Hoje com fusos diferentes; calendário; tempo em atraso;
mudança automática de aba lógica no relógio sem trocar o filtro escolhido;
liberação do timer ao fechar a janela. Os status visuais não modificam o estado
da fila nem geram notificações duplicadas. Recorrências mantêm a semântica de
agendamento da próxima ocorrência; o filtro considera a data atual desse registro.
Prévia isolada dos cartões e abas: `/tmp/monitor-global-agenda-preview.png`.

Versão 1.2.0: movimento e clique reais de mouse na barra; tooltip com nome/fonte/
horário; abertura do ativo correto por CLI; reutilização do modal; fechamento do
detalhe preservando a janela; troca de período sem aceitar respostas antigas;
agrupamento mensal pelo último fechamento e consulta de data/valor pelo mouse.
Histórico: cache por ativo/período, limpeza ao excluir, falhas sem inventar dados,
limites de cota, rejeição de valores inválidos e paginação anual da Coinbase com
até 299 intervalos por chamada. Fonte mensal Alpha Vantage é explicitamente indicada.

Consultas anuais públicas reais confirmadas sem chaves: PETR4/brapi (247 pontos),
USD-BRL/Frankfurter (367 antes do filtro de datas) e BTC-USD/Coinbase (366 pontos).
Captura da abertura pelo clique na barra: `/tmp/monitor-global-chart-preview.png`.

Regressão 1.1.1: trocar uma cidade existente substitui o rótulo anterior junto com
nome/coordenadas; rótulos personalizados continuam editáveis. Alterar somente o
rótulo do clima preserva a previsão; trocar coordenadas invalida os dados anteriores.
Edições de câmbio, cripto e bolsa são verificadas no formulário, no estado publicado
e na barra real do GNOME. Trocar código limpa preço/histórico anteriores e rejeita
respostas pendentes do ativo antigo.

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
