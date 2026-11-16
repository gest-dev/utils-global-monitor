# Monitor Global

Aplicativo nativo e extensão para **Ubuntu/GNOME 50**, com moedas, cripto, bolsa,
clima, agenda, alertas e notificações. Projeto independente do `sys-monitor`,
com UUID próprio: `utils-global-monitor@gezer.local`.

Não requer Node.js, navegador, servidor remoto ou pacotes pip. A barra usa GJS;
a janela e o serviço local usam Python 3, GTK 4 e libadwaita.

## Instalar e abrir

```bash
cd /var/www/html/utils-global-monitor
python3 instalar.py
python3 abrir.py
```

O pacote pronto está em `dist/`. O instalador trabalha na sua conta, sem sudo e
sem downloads. Instala também o atalho **Monitor Global** no menu de aplicativos
e a inicialização do serviço de lembretes com a sessão. Preserva as outras
extensões e cria backup da extensão anterior nas atualizações.

Para carregar código novo na barra, salve o trabalho, saia da sessão do Ubuntu
e entre novamente. A janela pode ser aberta imediatamente pelo comando acima.
O instalador não encerra sua sessão. Dependências: GNOME 50, GJS, Python 3 com
PyGObject, GTK 4.10+ e libadwaita 1.4+ (presentes na máquina usada na validação).

## Escolher o que acompanhar

- **Moedas:** vários pares, como USD-BRL, EUR-BRL e GBP-BRL; referência de compra,
  venda e variação quando fornecidas pela fonte; conversor na janela de detalhes.
- **Cripto:** mercados públicos da Coinbase Exchange, como BTC-USD, ETH-USD e
  SOL-USD. Pares em BRL são convertidos do mercado USD com a referência cambial;
  a variação indicada permanece a do mercado USD, identificada nos detalhes.
- **Bolsa:** B3 pela brapi. PETR4, VALE3, MGLU3 e ITUB4 funcionam sem chave;
  outros ativos exigem chave gratuita e cobertura do plano. Alpha Vantage permite
  acompanhar fechamento de outros mercados com chave gratuita e moeda informada
  no cadastro. Chaves são configuradas em **Integrações**.
- **Clima:** pesquisa por cidade, estado e país ou coordenadas manuais; temperatura,
  sensação térmica, umidade, vento, probabilidade de chuva e previsão de 7 dias.
  A cidade inicial é São Paulo, identificada explicitamente e substituível.

Em cada indicador, escolha **Monitorar**, **Mostrar na barra**, edite o cadastro
ou ajuste a ordem pelas setas. Detalhes mostram fonte e data/hora do dado.
O menu da barra abre a janela, a agenda e a personalização.

Em **Clima → Adicionar cidade**, digite o nome e pressione Enter ou Buscar.
A pesquisa automática começa com 3 letras, após uma breve pausa. Use, por exemplo,
`Campinas, SP` ou `Paris, França`; o filtro de país ajuda a distinguir homônimos.
Clique no resultado com município, estado e país, ajuste o nome/rótulo se desejar
e salve. Nenhum resultado é escolhido automaticamente. As coordenadas ficam em
uma seção opcional. É possível cadastrar e fixar várias cidades.

Ao selecionar outra cidade, inclusive na edição, o nome, as coordenadas e o rótulo
da barra são atualizados juntos. O rótulo do clima tem **de 1 a 3 letras**: Vitória
da Conquista → `VDC`, São Paulo → `SP`, Salvador → `SAL`. Para nomes com várias
palavras, são usadas até três iniciais, incluindo as preposições; para uma palavra,
até três letras iniciais. Você pode personalizar a sigla respeitando o limite.
O campo e a validação do cadastro aplicam essa regra. Rótulos antigos longos são
convertidos ao iniciar o aplicativo; os anteriores ficam registrados nos metadados
locais para referência. O nome completo permanece na janela e no tooltip da barra.
Alterar somente o rótulo preserva os dados meteorológicos; trocar as coordenadas
descarta o tempo da cidade anterior. Em moedas, cripto e bolsa, o campo **Código /
par** determina a consulta e a identificação na barra; o nome é personalizável.
Trocar o código descarta preços e histórico do ativo anterior.

**Usar localização do Ubuntu** é opcional e aparece quando o GeoClue está disponível
(`gir1.2-geoclue-2.0`). O botão solicita uma localização aproximada em nível de cidade,
respeitando a autorização do Ubuntu. É uma captura única, sem acompanhamento contínuo.
Se o serviço estiver desativado, sem permissão ou indisponível, use a busca por cidade.
Uma cidade já cadastrada é preservada em caso de falha. A localização automática
é salva como “Localização atual”, com nome editável; não é convertida em nome de cidade.

Consultas de câmbio são agrupadas. O serviço mantém cache e respeita intervalos
mínimos: câmbio 1 min, cripto 2 min, clima 15 min, brapi 30 min e Alpha Vantage 6 h.
O intervalo preferido padrão é 5 min; as fontes podem atualizar mais lentamente.
O Alpha Vantage tem um teto local de 25 chamadas/dia, compartilhado pelos ativos
deste aplicativo. A cota real também considera outros aplicativos usando a chave.

Passe o mouse sobre um valor da barra para ver nome, fonte, horário e situação.
Clique no valor para abrir a aplicação na página do ativo, com o modal de detalhes.
A seta à direita abre o menu geral; os ativos no menu também abrem seus detalhes.
Cliques repetidos no mesmo ativo reaproveitam a janela e o modal.

O gráfico oferece **7 dias, 1 mês, 3 meses, 6 meses e 1 ano**, com agrupamento
**diário, semanal ou mensal** pelo último fechamento disponível de cada intervalo.
Mostra os eixos de data/valor, mínimo, máximo, variação entre os pontos exibidos e
data/valor ao passar o mouse. O período realmente disponível e a fonte ficam visíveis.
O botão de atualização consulta novamente; o cache é separado por ativo e período
(15 min; 6 h para Alpha Vantage) e invalidado quando o código muda.

Histórico de câmbio: AwesomeAPI até 6 meses; para 1 ano, referências diárias
Frankfurter, que podem diferir da cotação atual. Cripto: fechamentos da Coinbase
em mercado direto, com paginação para o ano completo. B3: brapi conforme a cobertura
e o plano; PETR4 anual foi validado sem chave. Alpha Vantage: chave gratuita e
cota compartilhada, fechamentos diários até 3 meses e mensais para 6 meses/1 ano.
Não é necessário contratar planos para usar o aplicativo.

Quando a fonte não oferece o histórico, o modal informa a limitação e usa apenas
amostras locais reais dentro do período. Não são inventados preços passados nem
taxas históricas de conversão BRL. Até 500 amostras locais são mantidas por indicador.
A previsão do tempo fica nos detalhes do clima.

Também é possível abrir um ativo pelo identificador com `python3 abrir.py --asset=ID`.
O serviço local oferece o método D-Bus `OpenAsset` para a mesma navegação.

Em falhas, o valor anterior é preservado e marcado na janela; o menu da barra
informa fontes indisponíveis. As referências podem refletir o último pregão e
não são preços garantidos de uma operação. Não há ordens, carteira, custódia,
conexão com corretora nem recomendação de investimento.

## Agenda e notificações

Crie lembretes com título, descrição, data, horário, categoria, fuso horário,
antecedência e repetição: única, diária, semanal, dias úteis ou mensal.
O padrão segue o fuso do sistema. A agenda oferece abas **Hoje, Próximos, Atrasados,
Concluídos, Pausados, Todos e Calendário**, com contadores e busca por título,
descrição ou categoria. É possível editar, concluir, pausar e adiar.

Os cartões mostram status com cores: **Agendado**, **Aviso gerado**, **Atrasado**,
**Concluído** e **Pausado**. Ao passar do horário, um lembrete ativo não concluído
aparece em Atrasados, inclusive quando seu aviso já foi gerado. A geração do aviso
não marca a tarefa como concluída nem comprova entrega a um destino externo.
Lembretes pausados e concluídos ficam nas abas correspondentes. Próximos mostra
apenas horários futuros; Hoje respeita o fuso de cada lembrete. Os contadores
acompanham a busca. A aba Calendário permite consultar uma data específica.

Com a agenda aberta, o relógio é verificado a cada 5 segundos e a lista é atualizada
ao vencer um horário, preservando a aba e a busca. Atrasados também mostra há quanto
tempo o lembrete venceu. Não é necessário reabrir a janela ou aguardar as cotações.
Em lembretes recorrentes, o agendamento segue mostrando a próxima ocorrência
calculada pelo serviço; o filtro usa o horário atual desse agendamento.

O serviço verifica agendamentos a cada 5 s e continua ativo ao fechar a janela.
Notificações nativas permitem abrir o monitor, concluir o aviso e adiar 10 min.
Agendamentos vencidos durante suspensão ou desligamento aparecem ao retornar à
sessão; recorrências vencidas geram uma ocorrência pendente e avançam para a
próxima data. Recorrência mensal preserva o dia original quando possível.

**Alertas** acompanham valor/temperatura, variação percentual ou chance de chuva,
com limite maior/menor e intervalo mínimo entre avisos. Dependem das consultas
da fonte, portanto também de sua frequência e atraso. A métrica indisponível não
dispara alertas. Som e notificações locais podem ser configurados em Aparência.

Para avisar com o computador desligado, seria necessário executar o agendamento
em outro equipamento/servidor. A versão entregue funciona na sessão local.
Desabilitar apenas o indicador da extensão não encerra o serviço da agenda.

## APIs externas opcionais

Nenhum destino externo vem configurado ou ativado. Cadastre em **Integrações**,
ative o destino e selecione-o nos lembretes/alertas desejados. Há botão de teste
e histórico de tentativas; a aceitação HTTP é identificada como **Aceito pela API**,
sem afirmar que o destinatário já recebeu ou leu a mensagem.

- **Webhook HTTP:** URL, destinatário, credencial, cabeçalho e prefixo de
  autenticação configuráveis. O corpo padrão inclui `event_id`, `destination`,
  `type`, `title`, `body` e os horários aplicáveis. Também aceita corpo JSON
  personalizado com `{{title}}`, `{{body}}`, `{{destination}}`, `{{event_id}}` e
  `{{due}}`. Exemplo: `{"number":"{{destination}}","text":"{{title}} — {{body}}"}`.
  Para APIs com X-API-Key, escolha esse cabeçalho e deixe o prefixo vazio.
- **WhatsApp oficial:** token, ID do número remetente, destinatário, versão da
  Graph API e template aprovado com dois parâmetros de texto no corpo (título e
  descrição). A conta e o template são configurados pelo usuário na Meta; o
  aplicativo não faz esse cadastro. Podem existir cobranças da Meta.

Envios usam chave `Idempotency-Key`, quatro tentativas com espera crescente,
registro do resultado e repetição manual após falha. A API de destino precisa
respeitar a chave para garantir deduplicação no caso de timeout após aceitação.
Redirecionamentos HTTP não são seguidos, evitando encaminhar credenciais a outra
URL. Prefira HTTPS para proteger mensagens e tokens.

As integrações foram validadas com um servidor HTTP local de teste e requisições
WhatsApp simuladas. Não houve envio real de WhatsApp ou a terceiros.

## Aparência e dados locais

Visual escuro com destaque verde, cartões, gráficos e ícones SVG originais.
O logo do aplicativo usa verde, fundo escuro e um gráfico branco sobre um globo.
O PNG original com transparência está em `extension/icons/monitor-global-logo-v1.png`;
é usado na marca da janela, no atalho do Ubuntu e nas notificações. A geração foi
feita com image_gen; o prompt está em `branding/prompt-logo-v1.txt`. O SVG anterior
foi preservado, assim como os ícones simbólicos dos módulos.
Temas claro/escuro/sistema, cores por módulo, fonte e ícones da janela e da barra,
espaçamento, formatos, variação e quantidade de indicadores são configuráveis.
Perfis Trabalho, Mercado e Viagem ajustam os favoritos. Há prévia da barra e
restauração da aparência sem apagar agenda ou indicadores.

Em **Aparência → Posição na barra do Ubuntu**, escolha **Esquerda**, **Centro · antes
do relógio** ou **Direita**. A alteração é imediata com a extensão atualizada carregada;
o indicador e seu menu são movidos juntos. A escolha é preservada ao reiniciar a sessão.
Depois de instalar código novo da extensão, pode ser necessário sair e entrar no Ubuntu.

Dados ficam em `${XDG_DATA_HOME:-~/.local/share}/utils-global-monitor/monitor.db`.
O banco tem permissão 600 e a pasta é privada. Credenciais ficam nesse banco
local; não há promessa de criptografia do arquivo. O backup JSON exclui os campos
de chaves/tokens e inclui configurações, indicadores, agenda, alertas e destinos.
A importação mescla IDs, valida tudo antes de gravar e deixa os destinos externos
importados desativados. O histórico da fila é limitado a 2.000 eventos concluídos.

## Desenvolvimento e validação

```bash
bash empacotar.sh
```

Testes sem rede: modelos, parser das fontes, horários, recorrência, concorrência,
fila de entregas, limites, backup, credenciais e adaptadores. Para a integração
gráfica e HTTP em uma sessão isolada:

```bash
dbus-run-session -- gnome-shell-test-tool --headless \
  --extra-filter org.gezer.MonitorGlobal \
  --extra-filter org.gezer.MonitorGlobal.Test \
  --extension "$PWD/dist/utils-global-monitor@gezer.local.shell-extension.zip" \
  "$PWD/tests/shell.js"
```

A sessão temporária tem seus próprios dados e D-Bus. O teste da interface usa
fixtures identificadas e isoladas no processo de teste; a aplicação distribuída
consulta as fontes reais. A captura fica em `/tmp/monitor-global-preview.png`.
Veja [VALIDACAO.md](VALIDACAO.md).

## Fontes e condições

- [AwesomeAPI](https://docs.awesomeapi.com.br/api-de-moedas) — moedas e histórico.
- [Frankfurter](https://frankfurter.dev/) — referência diária de câmbio como alternativa.
- [Coinbase Exchange](https://docs.cdp.coinbase.com/api-reference/exchange-api/rest-api/products/get-product-stats) — preços e estatísticas de cripto.
- [brapi](https://brapi.dev/faq/api-e-gratis-mesmo) — B3, cobertura e planos gratuitos.
- [Alpha Vantage](https://www.alphavantage.co/support/) — chave gratuita e limite diário.
- [Open-Meteo](https://open-meteo.com/) — clima, atribuição CC BY 4.0 e API gratuita para uso não comercial.
- [Busca de cidades Open-Meteo](https://open-meteo.com/en/docs/geocoding-api) — geocodificação com dados GeoNames.
- [GeoClue](https://www.freedesktop.org/software/geoclue/docs/libgeoclue/GClueSimple.html) — localização aproximada opcional do Ubuntu.
- [WhatsApp Business Platform](https://business.whatsapp.com/products/platform-pricing) — condições de envio e preços.

Fontes gratuitas têm limites e podem alterar condições/cobertura. O aplicativo
não contrata planos, não paga serviços e não cria contas automaticamente.
