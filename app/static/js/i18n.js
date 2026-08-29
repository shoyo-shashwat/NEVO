/* =========================================================================
   NEVO / BRICS People First — client-side i18n
   -------------------------------------------------------------------------
   Whole-app UI translation with no build step and no server dependency.

   HOW IT WORKS
   - Loaded once from base.html, so every page that extends base.html gets it.
   - Walks the rendered DOM, and for any text / placeholder / title / aria-label
     whose (whitespace-normalised) English matches a key in DICT below, swaps in
     the chosen language. Unmatched text (citizen reports, place names, people's
     names, free-text decision reasons) is left exactly as-is.
   - A few interpolated strings ("5 people", "80% match") are handled by RULES.
   - The choice is stored in localStorage AND a cookie ("nevo_lang") so the
     server can read it later if we ever move to server-side rendering.

   EXTENDING
   - Add another `"English string": { hi, pt, ru }` entry to DICT. That's it —
     no template changes. Keys are the English text with runs of whitespace
     collapsed to one space and the ends trimmed.
   ===================================================================== */
(function () {
  'use strict';

  var LANGS = { en: 'English', hi: 'हिन्दी', pt: 'Português', ru: 'Русский' };
  var STORAGE_KEY = 'nevo_lang';
  var DEFAULT = 'en';

  /* ---- exact-match dictionary --------------------------------------- */
  var DICT = {
    /* ---------- header / nav / footer (base.html) ---------- */
    'Dashboard':       { hi: 'डैशबोर्ड', pt: 'Painel', ru: 'Панель' },
    'Map':             { hi: 'मानचित्र', pt: 'Mapa', ru: 'Карта' },
    'Projects':        { hi: 'परियोजनाएँ', pt: 'Projetos', ru: 'Проекты' },
    'Admin':           { hi: 'व्यवस्थापक', pt: 'Admin', ru: 'Администратор' },
    'Home':            { hi: 'होम', pt: 'Início', ru: 'Главная' },
    'Report':          { hi: 'रिपोर्ट', pt: 'Reportar', ru: 'Сообщить' },
    'Community':       { hi: 'समुदाय', pt: 'Comunidade', ru: 'Сообщество' },
    'My Timeline':     { hi: 'मेरी टाइमलाइन', pt: 'Minha linha do tempo', ru: 'Моя хроника' },
    'Switch':          { hi: 'बदलें', pt: 'Trocar', ru: 'Сменить' },
    'Demo login':      { hi: 'डेमो लॉगिन', pt: 'Login de demonstração', ru: 'Демо-вход' },
    'Demo data — for illustration purposes only · BRICS People First':
      { hi: 'डेमो डेटा — केवल उदाहरण के लिए · BRICS People First',
        pt: 'Dados de demonstração — apenas para ilustração · BRICS People First',
        ru: 'Демо-данные — только для примера · BRICS People First' },

    /* ---------- landing / role select ---------- */
    'Report a development problem in your own words. See whether your community already asked for the same thing. Follow it all the way to a government decision — and find out whether it actually got better.':
      { hi: 'अपनी भाषा में कोई विकास समस्या बताइए। देखिए कि क्या आपके समुदाय ने पहले भी यही माँग की है। इसे सरकारी निर्णय तक पूरे रास्ते ट्रैक कीजिए — और जानिए कि हालात वाकई सुधरे या नहीं।',
        pt: 'Relate um problema de desenvolvimento com suas próprias palavras. Veja se a sua comunidade já pediu a mesma coisa. Acompanhe até a decisão do governo — e descubra se realmente melhorou.',
        ru: 'Опишите проблему развития своими словами. Узнайте, просило ли ваше сообщество о том же раньше. Проследите путь до решения властей — и выясните, стало ли действительно лучше.' },
    'Report a problem':      { hi: 'समस्या दर्ज करें', pt: 'Relatar um problema', ru: 'Сообщить о проблеме' },
    'See what your community needs':
      { hi: 'देखें आपके समुदाय को क्या चाहिए', pt: 'Veja o que a sua comunidade precisa', ru: 'Узнайте, что нужно сообществу' },
    'From individual voice to visible outcome':
      { hi: 'एक आवाज़ से दिखते परिणाम तक', pt: 'Da voz individual ao resultado visível', ru: 'От одного голоса к видимому результату' },
    'Every report is tracked from submission through community demand to government decision.':
      { hi: 'हर रिपोर्ट को दर्ज होने से लेकर सामूहिक माँग और सरकारी निर्णय तक ट्रैक किया जाता है।',
        pt: 'Cada relato é acompanhado desde o envio até a demanda da comunidade e a decisão do governo.',
        ru: 'Каждое обращение отслеживается от подачи до коллективного запроса и решения властей.' },
    'Countries live':        { hi: 'सक्रिय देश', pt: 'Países ativos', ru: 'Стран онлайн' },
    'Issue categories':      { hi: 'समस्या श्रेणियाँ', pt: 'Categorias de problemas', ru: 'Категорий проблем' },
    'Timeline stages tracked': { hi: 'ट्रैक किए गए चरण', pt: 'Etapas acompanhadas', ru: 'Отслеживаемых этапов' },
    'Who are you signing in as?':
      { hi: 'आप किस रूप में साइन इन कर रहे हैं?', pt: 'Você está entrando como?', ru: 'Кто вы при входе?' },
    'Select your role to access customized tools and dashboards tailored to your civic responsibilities and community engagement needs.':
      { hi: 'अपनी भूमिका चुनें ताकि आपकी नागरिक ज़िम्मेदारियों और सामुदायिक भागीदारी के अनुरूप उपकरण और डैशबोर्ड मिल सकें।',
        pt: 'Selecione a sua função para acessar ferramentas e painéis adaptados às suas responsabilidades cívicas e ao engajamento com a comunidade.',
        ru: 'Выберите роль, чтобы получить инструменты и панели, подходящие вашим гражданским задачам и участию в жизни сообщества.' },
    'Citizen':               { hi: 'नागरिक', pt: 'Cidadão', ru: 'Гражданин' },
    'Report local issues, track infrastructure progress, and engage directly with your community representatives.':
      { hi: 'स्थानीय समस्याएँ दर्ज करें, बुनियादी ढाँचे की प्रगति देखें और अपने प्रतिनिधियों से सीधे जुड़ें।',
        pt: 'Relate problemas locais, acompanhe o progresso da infraestrutura e fale diretamente com os seus representantes.',
        ru: 'Сообщайте о местных проблемах, следите за развитием инфраструктуры и общайтесь с вашими представителями.' },
    'Continue as Citizen':   { hi: 'नागरिक के रूप में जारी रखें', pt: 'Continuar como Cidadão', ru: 'Продолжить как гражданин' },
    'MP / Policymaker':      { hi: 'सांसद / नीति-निर्माता', pt: 'Parlamentar / Legislador', ru: 'Депутат / политик' },
    'Mp / Policymaker':      { hi: 'सांसद / नीति-निर्माता', pt: 'Parlamentar / Legislador', ru: 'Депутат / политик' },
    'Review citizen demands, allocate resources, and communicate transparently with your constituency.':
      { hi: 'नागरिकों की माँगें देखें, संसाधन आवंटित करें और अपने क्षेत्र से पारदर्शी ढंग से संवाद करें।',
        pt: 'Analise as demandas dos cidadãos, aloque recursos e comunique-se de forma transparente com o seu eleitorado.',
        ru: 'Изучайте запросы граждан, распределяйте ресурсы и открыто общайтесь с вашим округом.' },
    'Continue as MP':        { hi: 'सांसद के रूप में जारी रखें', pt: 'Continuar como Parlamentar', ru: 'Продолжить как депутат' },
    'Planning Officer':      { hi: 'योजना अधिकारी', pt: 'Agente de Planejamento', ru: 'Специалист по планированию' },
    'Manage infrastructure projects, evaluate technical feasibility, and update project milestones.':
      { hi: 'बुनियादी ढाँचा परियोजनाएँ प्रबंधित करें, तकनीकी व्यवहार्यता आँकें और परियोजना के पड़ाव अपडेट करें।',
        pt: 'Gerencie projetos de infraestrutura, avalie a viabilidade técnica e atualize os marcos do projeto.',
        ru: 'Управляйте инфраструктурными проектами, оценивайте техническую осуществимость и обновляйте этапы.' },
    'Continue as Officer':   { hi: 'अधिकारी के रूप में जारी रखें', pt: 'Continuar como Agente', ru: 'Продолжить как специалист' },
    'Step 1': { hi: 'चरण 1', pt: 'Etapa 1', ru: 'Шаг 1' },
    'Step 2': { hi: 'चरण 2', pt: 'Etapa 2', ru: 'Шаг 2' },
    'Step 3': { hi: 'चरण 3', pt: 'Etapa 3', ru: 'Шаг 3' },
    'Step 4': { hi: 'चरण 4', pt: 'Etapa 4', ru: 'Шаг 4' },
    'You report it': { hi: 'आप इसे दर्ज करते हैं', pt: 'Você relata', ru: 'Вы сообщаете' },
    'Speak, type, or message it — in your own language. No government-style form.':
      { hi: 'बोलिए, टाइप कीजिए या संदेश भेजिए — अपनी भाषा में। कोई सरकारी फ़ॉर्म नहीं।',
        pt: 'Fale, digite ou envie uma mensagem — no seu idioma. Sem formulário burocrático.',
        ru: 'Скажите, напишите или отправьте сообщение — на своём языке. Без казённых бланков.' },
    'It becomes collective demand':
      { hi: 'यह सामूहिक माँग बन जाती है', pt: 'Torna-se demanda coletiva', ru: 'Это становится коллективным запросом' },
    'We find everyone else reporting the same need and show the real scale of it.':
      { hi: 'हम उन सभी को ढूँढते हैं जिन्होंने वही ज़रूरत बताई है और उसका असली पैमाना दिखाते हैं।',
        pt: 'Encontramos todos os outros que relatam a mesma necessidade e mostramos a real dimensão.',
        ru: 'Мы находим всех, кто сообщил о той же нужде, и показываем её реальный масштаб.' },
    'Government reviews it':
      { hi: 'सरकार इसकी समीक्षा करती है', pt: 'O governo analisa', ru: 'Власти рассматривают' },
    'Officials see the evidence and record a decision — with a reason you can read.':
      { hi: 'अधिकारी साक्ष्य देखते हैं और निर्णय दर्ज करते हैं — एक कारण के साथ जिसे आप पढ़ सकते हैं।',
        pt: 'As autoridades veem as evidências e registram uma decisão — com um motivo que você pode ler.',
        ru: 'Чиновники изучают доказательства и фиксируют решение — с причиной, которую вы можете прочитать.' },
    'You see the outcome':
      { hi: 'आप परिणाम देखते हैं', pt: 'Você vê o resultado', ru: 'Вы видите результат' },
    'Track implementation and find out whether the original problem actually improved.':
      { hi: 'क्रियान्वयन ट्रैक कीजिए और जानिए कि मूल समस्या वाकई सुधरी या नहीं।',
        pt: 'Acompanhe a execução e descubra se o problema original realmente melhorou.',
        ru: 'Следите за реализацией и узнайте, действительно ли исходная проблема решилась.' },
    '© 2026 BRICS People First. Empowering civic resolution.':
      { hi: '© 2026 BRICS People First. नागरिक समाधान को सशक्त बनाना।',
        pt: '© 2026 BRICS People First. Fortalecendo a resolução cívica.',
        ru: '© 2026 BRICS People First. Сила гражданских решений.' },
    'Privacy Policy':    { hi: 'गोपनीयता नीति', pt: 'Política de Privacidade', ru: 'Политика конфиденциальности' },
    'Terms of Service':  { hi: 'सेवा की शर्तें', pt: 'Termos de Serviço', ru: 'Условия использования' },
    'Contact':           { hi: 'संपर्क', pt: 'Contato', ru: 'Контакты' },

    /* ---------- login ---------- */
    'Continue as': { hi: 'जारी रखें', pt: 'Continuar como', ru: 'Продолжить как' },
    'CITIZEN': { hi: 'नागरिक', pt: 'CIDADÃO', ru: 'ГРАЖДАНИН' },
    'MP / POLICYMAKER': { hi: 'सांसद / नीति-निर्माता', pt: 'PARLAMENTAR', ru: 'ДЕПУТАТ' },
    'PLANNING OFFICER': { hi: 'योजना अधिकारी', pt: 'AGENTE DE PLANEJAMENTO', ru: 'СПЕЦИАЛИСТ ПО ПЛАНИРОВАНИЮ' },
    'Select your region to access local civic tools and community resources.':
      { hi: 'स्थानीय नागरिक उपकरणों और सामुदायिक संसाधनों तक पहुँचने के लिए अपना क्षेत्र चुनें।',
        pt: 'Selecione a sua região para acessar ferramentas cívicas locais e recursos da comunidade.',
        ru: 'Выберите регион для доступа к местным гражданским инструментам и ресурсам сообщества.' },
    'Back to roles': { hi: 'भूमिकाओं पर वापस', pt: 'Voltar às funções', ru: 'Назад к ролям' },
    'India': { hi: 'भारत', pt: 'Índia', ru: 'Индия' },
    'Brazil': { hi: 'ब्राज़ील', pt: 'Brasil', ru: 'Бразилия' },
    'Russia': { hi: 'रूस', pt: 'Rússia', ru: 'Россия' },
    'Need help?': { hi: 'मदद चाहिए?', pt: 'Precisa de ajuda?', ru: 'Нужна помощь?' },
    'Contact Support': { hi: 'सहायता से संपर्क करें', pt: 'Fale com o suporte', ru: 'Связаться с поддержкой' },
    "You're signed in": { hi: 'आप साइन इन हैं', pt: 'Você entrou', ru: 'Вы вошли' },
    'Welcome back to BRICS People First.':
      { hi: 'BRICS People First में आपका फिर से स्वागत है।',
        pt: 'Bem-vindo de volta ao BRICS People First.',
        ru: 'С возвращением в BRICS People First.' },
    'Demo identity': { hi: 'डेमो पहचान', pt: 'Identidade de demonstração', ru: 'Демо-профиль' },
    'Go to Dashboard': { hi: 'डैशबोर्ड पर जाएँ', pt: 'Ir para o painel', ru: 'Перейти к панели' },
    'Start over': { hi: 'फिर से शुरू करें', pt: 'Começar de novo', ru: 'Начать заново' },

    /* ---------- citizen home ---------- */
    'India · Brazil · Russia': { hi: 'भारत · ब्राज़ील · रूस', pt: 'Índia · Brasil · Rússia', ru: 'Индия · Бразилия · Россия' },
    'Tell us what your community needs':
      { hi: 'बताइए आपके समुदाय को क्या चाहिए', pt: 'Conte-nos o que a sua comunidade precisa', ru: 'Расскажите, что нужно вашему сообществу' },
    'Your voice shapes local development priorities — and you can follow exactly what happens to it.':
      { hi: 'आपकी आवाज़ स्थानीय विकास प्राथमिकताओं को आकार देती है — और आप देख सकते हैं कि उसका क्या हुआ।',
        pt: 'A sua voz define as prioridades de desenvolvimento local — e você pode acompanhar exatamente o que acontece com ela.',
        ru: 'Ваш голос формирует приоритеты местного развития — и вы можете точно проследить его судьбу.' },
    'Community issues': { hi: 'सामुदायिक समस्याएँ', pt: 'Problemas da comunidade', ru: 'Проблемы сообщества' },
    'Demand map': { hi: 'माँग मानचित्र', pt: 'Mapa de demandas', ru: 'Карта запросов' },
    'My timeline': { hi: 'मेरी टाइमलाइन', pt: 'Minha linha do tempo', ru: 'Моя хроника' },

    /* ---------- community issues ---------- */
    'Every card below is a collective demand — many individual reports on the same problem, merged into one voice. The bigger the numbers, the stronger the case for government action.':
      { hi: 'नीचे हर कार्ड एक सामूहिक माँग है — एक ही समस्या पर कई रिपोर्टें, एक आवाज़ में मिली हुई। संख्या जितनी बड़ी, सरकारी कार्रवाई का दावा उतना मज़बूत।',
        pt: 'Cada cartão abaixo é uma demanda coletiva — muitos relatos individuais sobre o mesmo problema, unidos em uma só voz. Quanto maiores os números, mais forte o argumento para a ação do governo.',
        ru: 'Каждая карточка ниже — коллективный запрос: множество отдельных обращений об одной проблеме, объединённых в один голос. Чем больше цифры, тем весомее основание для действий властей.' },
    'All': { hi: 'सभी', pt: 'Todos', ru: 'Все' },
    'No active community issues yet.': { hi: 'अभी कोई सक्रिय सामुदायिक समस्या नहीं।', pt: 'Ainda não há problemas ativos na comunidade.', ru: 'Активных проблем сообщества пока нет.' },
    'Be the first to report one.': { hi: 'पहले रिपोर्ट करने वाले बनें।', pt: 'Seja o primeiro a relatar.', ru: 'Сообщите первым.' },
    'Community says:': { hi: 'समुदाय कहता है:', pt: 'A comunidade diz:', ru: 'Сообщество сообщает:' },
    'Still happening?': { hi: 'अब भी हो रहा है?', pt: 'Ainda acontece?', ru: 'Всё ещё происходит?' },
    'Yes': { hi: 'हाँ', pt: 'Sim', ru: 'Да' },
    'Getting worse': { hi: 'और बिगड़ रहा है', pt: 'Piorando', ru: 'Ухудшается' },
    'Improved': { hi: 'सुधार हुआ', pt: 'Melhorou', ru: 'Улучшилось' },
    'Resolved': { hi: 'हल हो गया', pt: 'Resolvido', ru: 'Решено' },
    'Under Review': { hi: 'समीक्षाधीन', pt: 'Em análise', ru: 'На рассмотрении' },
    'Under review': { hi: 'समीक्षाधीन', pt: 'Em análise', ru: 'На рассмотрении' },
    'Active': { hi: 'सक्रिय', pt: 'Ativo', ru: 'Активно' },
    '↑ Increasing': { hi: '↑ बढ़ रहा है', pt: '↑ Crescendo', ru: '↑ Растёт' },

    /* ---------- report flow ---------- */
    'One more detail needed': { hi: 'एक और जानकारी चाहिए', pt: 'É preciso mais um detalhe', ru: 'Нужна ещё одна деталь' },
    "What's the problem?": { hi: 'समस्या क्या है?', pt: 'Qual é o problema?', ru: 'В чём проблема?' },
    'Describe it in your own words — voice or text, no government-style form to fill in.':
      { hi: 'इसे अपनी भाषा में बताइए — आवाज़ या टेक्स्ट से, कोई सरकारी फ़ॉर्म नहीं भरना।',
        pt: 'Descreva com suas próprias palavras — voz ou texto, sem formulário burocrático.',
        ru: 'Опишите своими словами — голосом или текстом, без казённых бланков.' },
    'Speak': { hi: 'बोलें', pt: 'Falar', ru: 'Говорить' },
    'Record voice': { hi: 'आवाज़ रिकॉर्ड करें', pt: 'Gravar voz', ru: 'Записать голос' },
    'Which area or neighbourhood?': { hi: 'कौन-सा क्षेत्र या मोहल्ला?', pt: 'Qual área ou bairro?', ru: 'Какой район или квартал?' },
    '(optional)': { hi: '(वैकल्पिक)', pt: '(opcional)', ru: '(необязательно)' },
    'Add a photo': { hi: 'फ़ोटो जोड़ें', pt: 'Adicionar uma foto', ru: 'Добавить фото' },
    'A photo makes it easier for officials to verify the issue.':
      { hi: 'फ़ोटो से अधिकारियों के लिए समस्या की पुष्टि करना आसान हो जाता है।',
        pt: 'Uma foto facilita a verificação do problema pelas autoridades.',
        ru: 'Фото помогает чиновникам быстрее проверить проблему.' },
    'Submit report': { hi: 'रिपोर्ट भेजें', pt: 'Enviar relato', ru: 'Отправить обращение' },
    'e.g. There is no clean water in our area for the past two months…':
      { hi: 'उदा. हमारे इलाके में पिछले दो महीने से साफ़ पानी नहीं है…',
        pt: 'ex. Não há água limpa na nossa área há dois meses…',
        ru: 'напр. В нашем районе два месяца нет чистой воды…' },
    'e.g. Nashik, Ward 12, Sinnar Block…':
      { hi: 'उदा. नाशिक, वार्ड 12, सिन्नर ब्लॉक…', pt: 'ex. Nashik, Zona 12, Bairro Sinnar…', ru: 'напр. Нашик, участок 12, район Синнар…' },
    'What happens next': { hi: 'आगे क्या होगा', pt: 'O que acontece a seguir', ru: 'Что дальше' },
    'We check if others already reported the same problem nearby.':
      { hi: 'हम देखते हैं कि आस-पास किसी और ने वही समस्या पहले दर्ज की है या नहीं।',
        pt: 'Verificamos se outras pessoas já relataram o mesmo problema por perto.',
        ru: 'Мы проверяем, сообщали ли о той же проблеме рядом другие.' },
    'Once enough voices join, it becomes visible to government reviewers.':
      { hi: 'जब पर्याप्त आवाज़ें जुड़ जाती हैं, यह सरकारी समीक्षकों को दिखने लगती है।',
        pt: 'Quando vozes suficientes se somam, o problema fica visível para os analistas do governo.',
        ru: 'Когда наберётся достаточно голосов, обращение становится видно проверяющим от властей.' },
    'You can follow the whole journey from your timeline, including the final outcome.':
      { hi: 'आप पूरी यात्रा अपनी टाइमलाइन से देख सकते हैं, अंतिम परिणाम सहित।',
        pt: 'Você pode acompanhar toda a jornada pela sua linha do tempo, incluindo o resultado final.',
        ru: 'Весь путь, включая итог, можно проследить в своей хронике.' },
    'Detecting your location…': { hi: 'आपका स्थान पता किया जा रहा है…', pt: 'Detectando sua localização…', ru: 'Определяем ваше местоположение…' },
    'Location detected ✓': { hi: 'स्थान मिल गया ✓', pt: 'Localização detectada ✓', ru: 'Местоположение определено ✓' },
    'Listening…': { hi: 'सुन रहे हैं…', pt: 'Ouvindo…', ru: 'Слушаем…' },
    'Done — review and submit.': { hi: 'हो गया — जाँचें और भेजें।', pt: 'Pronto — revise e envie.', ru: 'Готово — проверьте и отправьте.' },
    'Stop': { hi: 'रोकें', pt: 'Parar', ru: 'Стоп' },

    /* ---------- demand result ---------- */
    'Good news — this is already a known issue':
      { hi: 'अच्छी खबर — यह पहले से ज्ञात समस्या है', pt: 'Boa notícia — este problema já é conhecido', ru: 'Хорошая новость — эта проблема уже известна' },
    'Yes, join this issue': { hi: 'हाँ, इस समस्या से जुड़ें', pt: 'Sim, juntar-me a este problema', ru: 'Да, присоединиться' },
    'Not the same — start new': { hi: 'यह वही नहीं है — नया शुरू करें', pt: 'Não é o mesmo — criar novo', ru: 'Это не то — создать новое' },
    'Have a photo of this?': { hi: 'इसकी फ़ोटो है?', pt: 'Tem uma foto disso?', ru: 'Есть фото?' },
    '(optional, strengthens the case)': { hi: '(वैकल्पिक, दावा मज़बूत करता है)', pt: '(opcional, reforça o caso)', ru: '(необязательно, усиливает обращение)' },
    'Add evidence & join': { hi: 'साक्ष्य जोड़ें और जुड़ें', pt: 'Adicionar evidência e juntar-se', ru: 'Добавить доказательство и присоединиться' },
    'A few similar issues found nearby': { hi: 'आस-पास कुछ मिलती-जुलती समस्याएँ मिलीं', pt: 'Alguns problemas semelhantes encontrados por perto', ru: 'Рядом найдено несколько похожих проблем' },
    "We're not fully sure which one matches — pick whichever describes the same problem you reported, or start a new one if none fit.":
      { hi: 'हम पूरी तरह निश्चित नहीं कि कौन-सा मेल खाता है — वही चुनें जो आपकी बताई समस्या से मिलता हो, या कोई मेल न हो तो नया शुरू करें।',
        pt: 'Não temos certeza de qual corresponde — escolha o que descreve o mesmo problema que você relatou, ou crie um novo se nenhum servir.',
        ru: 'Мы не уверены, что подходит точно — выберите то, что описывает вашу проблему, или создайте новое, если ничего не подходит.' },
    'This one — join it': { hi: 'यही — इससे जुड़ें', pt: 'Este — juntar-se', ru: 'Это — присоединиться' },
    'None of these match — start a new issue': { hi: 'इनमें से कोई मेल नहीं — नई समस्या शुरू करें', pt: 'Nenhum corresponde — criar um novo problema', ru: 'Ничего не подходит — создать новую проблему' },
    "You're the first to report this": { hi: 'इसे रिपोर्ट करने वाले आप पहले हैं', pt: 'Você é o primeiro a relatar isso', ru: 'Вы сообщаете об этом первым' },
    'No matching community issue exists yet — your report will start a brand new one. Others reporting the same problem later will be grouped with yours.':
      { hi: 'अभी कोई मेल खाती सामुदायिक समस्या नहीं है — आपकी रिपोर्ट एक नई शुरू करेगी। बाद में वही समस्या बताने वाले आपके साथ जोड़े जाएँगे।',
        pt: 'Ainda não existe um problema correspondente na comunidade — o seu relato criará um novo. Outros que relatarem o mesmo problema depois serão agrupados com o seu.',
        ru: 'Подходящей проблемы сообщества пока нет — ваше обращение создаст новую. Позже сообщившие о том же будут объединены с вами.' },
    'Start this community issue': { hi: 'यह सामुदायिक समस्या शुरू करें', pt: 'Criar este problema da comunidade', ru: 'Создать проблему сообщества' },

    /* ---------- my timeline ---------- */
    "Every report you've submitted, and exactly where it stands — from AI understanding it, to joining a community issue, to a government decision, to whether the problem actually got better.":
      { hi: 'आपकी भेजी हर रिपोर्ट और वह कहाँ पहुँची — AI द्वारा समझे जाने से, सामुदायिक समस्या से जुड़ने, सरकारी निर्णय, और समस्या वाकई सुधरी या नहीं तक।',
        pt: 'Cada relato que você enviou e exatamente onde ele está — da compreensão pela IA, à junção a um problema da comunidade, à decisão do governo, até se o problema realmente melhorou.',
        ru: 'Каждое ваше обращение и его точный статус — от понимания ИИ до присоединения к проблеме сообщества, решения властей и того, стало ли лучше.' },
    'Reported': { hi: 'दर्ज किया', pt: 'Relatado', ru: 'Подано' },
    'Joined a community issue': { hi: 'सामुदायिक समस्या से जुड़े', pt: 'Juntou-se a um problema da comunidade', ru: 'Присоединено к проблеме сообщества' },
    'Government decided': { hi: 'सरकार ने निर्णय लिया', pt: 'Governo decidiu', ru: 'Власти приняли решение' },
    'Resolved & verified': { hi: 'हल और सत्यापित', pt: 'Resolvido e verificado', ru: 'Решено и подтверждено' },
    "You haven't submitted any reports yet.": { hi: 'आपने अभी तक कोई रिपोर्ट नहीं भेजी है।', pt: 'Você ainda não enviou nenhum relato.', ru: 'Вы ещё не отправляли обращений.' },
    'Submitted': { hi: 'भेजा गया', pt: 'Enviado', ru: 'Отправлено' },
    'AI understood': { hi: 'AI ने समझा', pt: 'IA entendeu', ru: 'ИИ понял' },
    'Waiting for more information': { hi: 'अधिक जानकारी की प्रतीक्षा', pt: 'Aguardando mais informações', ru: 'Ожидание дополнительной информации' },
    'Joined community issue': { hi: 'सामुदायिक समस्या से जुड़ा', pt: 'Juntou-se ao problema da comunidade', ru: 'Присоединено к проблеме сообщества' },
    'Finding similar reports…': { hi: 'मिलती-जुलती रिपोर्टें ढूँढी जा रही हैं…', pt: 'Procurando relatos semelhantes…', ru: 'Ищем похожие обращения…' },
    'Government review': { hi: 'सरकारी समीक्षा', pt: 'Análise do governo', ru: 'Рассмотрение властями' },
    'Decision': { hi: 'निर्णय', pt: 'Decisão', ru: 'Решение' },
    'Implementation': { hi: 'क्रियान्वयन', pt: 'Execução', ru: 'Реализация' },
    'Outcome': { hi: 'परिणाम', pt: 'Resultado', ru: 'Результат' },
    'Awaiting outcome data': { hi: 'परिणाम डेटा की प्रतीक्षा', pt: 'Aguardando dados de resultado', ru: 'Ожидание данных о результате' },
    'Prioritize': { hi: 'प्राथमिकता दें', pt: 'Priorizar', ru: 'Приоритет' },
    'Defer': { hi: 'स्थगित करें', pt: 'Adiar', ru: 'Отложить' },
    'Deprioritize': { hi: 'प्राथमिकता घटाएँ', pt: 'Despriorizar', ru: 'Понизить приоритет' },
    'NeedsValidation': { hi: 'सत्यापन आवश्यक', pt: 'Precisa de validação', ru: 'Требует проверки' },
    'Redirected': { hi: 'पुनर्निर्देशित', pt: 'Redirecionado', ru: 'Перенаправлено' },

    /* ---------- government dashboard ---------- */
    'MP priority queue': { hi: 'सांसद प्राथमिकता सूची', pt: 'Fila de prioridades do parlamentar', ru: 'Очередь приоритетов депутата' },
    'Officer implementation queue': { hi: 'अधिकारी क्रियान्वयन सूची', pt: 'Fila de execução do agente', ru: 'Очередь реализации специалиста' },
    'What needs your decision': { hi: 'किस पर आपका निर्णय चाहिए', pt: 'O que precisa da sua decisão', ru: 'Что требует вашего решения' },
    'What needs implementation follow-up': { hi: 'किस पर क्रियान्वयन अनुवर्तन चाहिए', pt: 'O que precisa de acompanhamento de execução', ru: 'Что требует контроля реализации' },
    '· ranked by real evidence — prioritise, defer, or reject':
      { hi: '· असली साक्ष्य के आधार पर क्रम — प्राथमिकता दें, स्थगित करें या अस्वीकार करें',
        pt: '· ordenado por evidência real — priorize, adie ou rejeite',
        ru: '· по реальным доказательствам — приоритет, отложить или отклонить' },
    "· where investment is missing, and what's already under way":
      { hi: '· जहाँ निवेश नहीं है, और जो पहले से चल रहा है',
        pt: '· onde falta investimento e o que já está em andamento',
        ru: '· где нет инвестиций и что уже идёт' },
    'Active signals': { hi: 'सक्रिय संकेत', pt: 'Sinais ativos', ru: 'Активные сигналы' },
    'Critical priority': { hi: 'अत्यावश्यक प्राथमिकता', pt: 'Prioridade crítica', ru: 'Критический приоритет' },
    'Awaiting decision': { hi: 'निर्णय की प्रतीक्षा', pt: 'Aguardando decisão', ru: 'Ожидает решения' },
    'Decisions recorded (all time)': { hi: 'दर्ज निर्णय (कुल)', pt: 'Decisões registradas (total)', ru: 'Записано решений (всего)' },
    'Top priorities': { hi: 'शीर्ष प्राथमिकताएँ', pt: 'Principais prioridades', ru: 'Главные приоритеты' },
    'Ranked by real evidence strength, severity and population affected — the ones most worth your attention first.':
      { hi: 'असली साक्ष्य की मज़बूती, गंभीरता और प्रभावित आबादी के आधार पर क्रम — जो पहले आपके ध्यान के योग्य हैं।',
        pt: 'Ordenados pela força da evidência real, gravidade e população afetada — os que mais merecem sua atenção primeiro.',
        ru: 'Отсортировано по силе доказательств, серьёзности и числу затронутых людей — то, что заслуживает внимания в первую очередь.' },
    'Emerging gaps': { hi: 'उभरती कमियाँ', pt: 'Lacunas emergentes', ru: 'Возникающие пробелы' },
    'Demand rising faster than coverage is keeping up with — worth getting ahead of before it becomes a top priority.':
      { hi: 'माँग कवरेज से तेज़ी से बढ़ रही है — शीर्ष प्राथमिकता बनने से पहले आगे रहना ठीक रहेगा।',
        pt: 'A demanda cresce mais rápido do que a cobertura acompanha — vale antecipar-se antes que vire uma prioridade máxima.',
        ru: 'Спрос растёт быстрее, чем покрытие — стоит опередить, пока это не стало главным приоритетом.' },
    'Awaiting your decision': { hi: 'आपके निर्णय की प्रतीक्षा', pt: 'Aguardando a sua decisão', ru: 'Ожидает вашего решения' },
    'A government actor opened these but no decision has been recorded yet.':
      { hi: 'किसी सरकारी अधिकारी ने इन्हें खोला पर अभी कोई निर्णय दर्ज नहीं हुआ।',
        pt: 'Um agente do governo abriu estes, mas nenhuma decisão foi registrada ainda.',
        ru: 'Чиновник открыл их, но решение ещё не зафиксировано.' },
    'No active demand signals for your area yet. New citizen reports will appear here once they cluster into a collective issue.':
      { hi: 'आपके क्षेत्र के लिए अभी कोई सक्रिय माँग संकेत नहीं। नई नागरिक रिपोर्टें सामूहिक समस्या बनते ही यहाँ दिखेंगी।',
        pt: 'Ainda não há sinais de demanda ativos para a sua área. Novos relatos de cidadãos aparecerão aqui quando formarem um problema coletivo.',
        ru: 'Активных сигналов спроса по вашему региону пока нет. Новые обращения появятся здесь, когда объединятся в коллективную проблему.' },
    'Investment & intervention gaps': { hi: 'निवेश और हस्तक्षेप की कमियाँ', pt: 'Lacunas de investimento e intervenção', ru: 'Пробелы в инвестициях и мерах' },
    'Demand with no matching investment on record, or only partial coverage — candidates for a new project proposal.':
      { hi: 'ऐसी माँग जिसके लिए रिकॉर्ड में कोई निवेश नहीं, या केवल आंशिक कवरेज — नई परियोजना प्रस्ताव के लिए उपयुक्त।',
        pt: 'Demanda sem investimento correspondente registrado, ou com cobertura apenas parcial — candidatas a uma nova proposta de projeto.',
        ru: 'Запросы без учтённых инвестиций или с частичным покрытием — кандидаты на новый проект.' },
    'Awaiting validation': { hi: 'सत्यापन की प्रतीक्षा', pt: 'Aguardando validação', ru: 'Ожидает проверки' },
    'Opened but not yet decided — these may need you to validate the evidence before an MP can act on them.':
      { hi: 'खोला गया पर अभी निर्णय नहीं — सांसद के कार्रवाई करने से पहले आपको साक्ष्य सत्यापित करना पड़ सकता है।',
        pt: 'Abertos mas ainda não decididos — pode ser necessário validar a evidência antes que um parlamentar aja.',
        ru: 'Открыто, но решения нет — возможно, нужно проверить доказательства, прежде чем депутат сможет действовать.' },
    'Your projects in progress': { hi: 'आपकी चल रही परियोजनाएँ', pt: 'Seus projetos em andamento', ru: 'Ваши текущие проекты' },
    "Everything you're tracking that isn't finished yet.":
      { hi: 'जो कुछ आप ट्रैक कर रहे हैं और अभी पूरा नहीं हुआ।',
        pt: 'Tudo o que você acompanha e que ainda não terminou.',
        ru: 'Всё, что вы отслеживаете и что ещё не завершено.' },
    'Open': { hi: 'खोलें', pt: 'Abrir', ru: 'Открыть' },
    "No projects in progress yet — propose one from a demand's evidence page.":
      { hi: 'अभी कोई परियोजना चालू नहीं — किसी माँग के साक्ष्य पृष्ठ से एक प्रस्तावित करें।',
        pt: 'Ainda não há projetos em andamento — proponha um a partir da página de evidências de uma demanda.',
        ru: 'Текущих проектов пока нет — предложите его на странице доказательств запроса.' },

    /* ---------- cluster card / priority ---------- */
    'Critical': { hi: 'अत्यावश्यक', pt: 'Crítico', ru: 'Критический' },
    'High': { hi: 'उच्च', pt: 'Alto', ru: 'Высокий' },
    'Medium': { hi: 'मध्यम', pt: 'Médio', ru: 'Средний' },
    'Low': { hi: 'निम्न', pt: 'Baixo', ru: 'Низкий' },
    'CRITICAL': { hi: 'अत्यावश्यक', pt: 'CRÍTICO', ru: 'КРИТИЧЕСКИЙ' },
    'HIGH': { hi: 'उच्च', pt: 'ALTO', ru: 'ВЫСОКИЙ' },
    'MEDIUM': { hi: 'मध्यम', pt: 'MÉDIO', ru: 'СРЕДНИЙ' },
    'LOW': { hi: 'निम्न', pt: 'BAIXO', ru: 'НИЗКИЙ' },
    'Review evidence': { hi: 'साक्ष्य की समीक्षा करें', pt: 'Analisar evidência', ru: 'Изучить доказательства' },
    /* investment-alignment states (replace('_',' ')|title) */
    'Unaddressed': { hi: 'अनसुलझा', pt: 'Não tratado', ru: 'Без внимания' },
    'Partially Addressed': { hi: 'आंशिक रूप से सुलझा', pt: 'Parcialmente tratado', ru: 'Частично охвачено' },
    'Aligned': { hi: 'संरेखित', pt: 'Alinhado', ru: 'Согласовано' },
    'Implementation Access Gap': { hi: 'क्रियान्वयन पहुँच अंतर', pt: 'Lacuna de acesso na execução', ru: 'Разрыв в доступе при реализации' },
    /* evidence confidence (replace('_',' ')|title) */
    'Needs Validation': { hi: 'सत्यापन आवश्यक', pt: 'Precisa de validação', ru: 'Требует проверки' },
    /* data freshness */
    'recent': { hi: 'हाल का', pt: 'recente', ru: 'свежие' },
    'stale': { hi: 'पुराना', pt: 'desatualizado', ru: 'устаревшие' },
    'unknown': { hi: 'अज्ञात', pt: 'desconhecido', ru: 'неизвестно' },

    /* ---------- decision workspace ---------- */
    'Back to evidence': { hi: 'साक्ष्य पर वापस', pt: 'Voltar às evidências', ru: 'Назад к доказательствам' },
    'Record your decision': { hi: 'अपना निर्णय दर्ज करें', pt: 'Registre a sua decisão', ru: 'Зафиксируйте решение' },
    'Previous decision': { hi: 'पिछला निर्णय', pt: 'Decisão anterior', ru: 'Предыдущее решение' },
    'What would you like to do?': { hi: 'आप क्या करना चाहेंगे?', pt: 'O que você gostaria de fazer?', ru: 'Что вы хотите сделать?' },
    'Prioritise / recommend action': { hi: 'प्राथमिकता दें / कार्रवाई की सिफ़ारिश करें', pt: 'Priorizar / recomendar ação', ru: 'Приоритет / рекомендовать действие' },
    'Move this forward — a Planning Officer can now propose or link a project to it.':
      { hi: 'इसे आगे बढ़ाएँ — अब कोई योजना अधिकारी इससे परियोजना जोड़ या प्रस्तावित कर सकता है।',
        pt: 'Levar adiante — um Agente de Planejamento pode agora propor ou vincular um projeto.',
        ru: 'Продвинуть дальше — специалист по планированию сможет предложить или привязать проект.' },
    'Not now — worth revisiting later, but not an active priority today.':
      { hi: 'अभी नहीं — बाद में देखने योग्य, पर आज सक्रिय प्राथमिकता नहीं।',
        pt: 'Agora não — vale rever depois, mas não é uma prioridade ativa hoje.',
        ru: 'Не сейчас — стоит вернуться позже, но сегодня не приоритет.' },
    'Deprioritise / reject': { hi: 'प्राथमिकता घटाएँ / अस्वीकार करें', pt: 'Despriorizar / rejeitar', ru: 'Понизить приоритет / отклонить' },
    'This will not be pursued. The reason you give is shown to affected citizens.':
      { hi: 'इस पर आगे कार्रवाई नहीं होगी। आपका दिया कारण प्रभावित नागरिकों को दिखाया जाता है।',
        pt: 'Isto não terá continuidade. O motivo informado é mostrado aos cidadãos afetados.',
        ru: 'Дальше не рассматривается. Указанная причина показывается затронутым гражданам.' },
    'Request validation': { hi: 'सत्यापन का अनुरोध करें', pt: 'Solicitar validação', ru: 'Запросить проверку' },
    'Send this to a Planning Officer to confirm the evidence before you decide.':
      { hi: 'निर्णय से पहले साक्ष्य की पुष्टि के लिए इसे योजना अधिकारी को भेजें।',
        pt: 'Envie a um Agente de Planejamento para confirmar a evidência antes de decidir.',
        ru: 'Отправьте специалисту по планированию для проверки доказательств до решения.' },
    'Redirect to another authority': { hi: 'दूसरे प्राधिकरण को भेजें', pt: 'Redirecionar para outra autoridade', ru: 'Перенаправить другому органу' },
    "This isn't within your remit — explain where it should go instead.":
      { hi: 'यह आपके अधिकार क्षेत्र में नहीं है — बताएँ कि इसे कहाँ जाना चाहिए।',
        pt: 'Isto não é da sua competência — explique para onde deve ir.',
        ru: 'Это вне вашей компетенции — укажите, куда это направить.' },
    'Validate evidence': { hi: 'साक्ष्य सत्यापित करें', pt: 'Validar evidência', ru: 'Проверить доказательства' },
    "Confirm the reported evidence checks out — this is the most common first step.":
      { hi: 'पुष्टि करें कि बताई गई बात सही है — यह सबसे आम पहला कदम है।',
        pt: 'Confirme que a evidência relatada procede — este é o primeiro passo mais comum.',
        ru: 'Подтвердите, что доказательства верны — это самый частый первый шаг.' },
    'Propose for action': { hi: 'कार्रवाई के लिए प्रस्तावित करें', pt: 'Propor para ação', ru: 'Предложить к действию' },
    'Recommend this to an MP for prioritisation.':
      { hi: 'प्राथमिकता के लिए इसे किसी सांसद को सुझाएँ।', pt: 'Recomende a um parlamentar para priorização.', ru: 'Рекомендовать депутату для приоритизации.' },
    'Redirect': { hi: 'पुनर्निर्देशित करें', pt: 'Redirecionar', ru: 'Перенаправить' },
    'This belongs with a different department or authority.':
      { hi: 'यह किसी अन्य विभाग या प्राधिकरण का विषय है।', pt: 'Isto pertence a outro departamento ou autoridade.', ru: 'Это относится к другому ведомству.' },
    'Reason': { hi: 'कारण', pt: 'Motivo', ru: 'Причина' },
    '— your own words, shown to affected citizens':
      { hi: '— आपके अपने शब्द, प्रभावित नागरिकों को दिखाए जाते हैं', pt: '— com suas palavras, mostrado aos cidadãos afetados', ru: '— вашими словами, показывается затронутым гражданам' },
    'Explain the reason for this decision…': { hi: 'इस निर्णय का कारण बताएँ…', pt: 'Explique o motivo desta decisão…', ru: 'Объясните причину этого решения…' },
    'e.g. Nashik Rural PHC Upgradation': { hi: 'उदा. नाशिक ग्रामीण PHC उन्नयन', pt: 'ex. Modernização da UBS rural de Nashik', ru: 'напр. Модернизация сельского ФАП в Нашике' },
    'e.g. 31 km to nearest facility': { hi: 'उदा. निकटतम सुविधा 31 किमी दूर', pt: 'ex. 31 km até a instalação mais próxima', ru: 'напр. 31 км до ближайшего объекта' },
    'e.g. New PHC opened, 4 km away': { hi: 'उदा. नया PHC खुला, 4 किमी दूर', pt: 'ex. Nova UBS aberta, a 4 km', ru: 'напр. Открыт новый ФАП, в 4 км' },
    'Link to existing project': { hi: 'मौजूदा परियोजना से जोड़ें', pt: 'Vincular a um projeto existente', ru: 'Привязать к существующему проекту' },
    '— none —': { hi: '— कोई नहीं —', pt: '— nenhum —', ru: '— нет —' },
    'Record decision': { hi: 'निर्णय दर्ज करें', pt: 'Registrar decisão', ru: 'Зафиксировать решение' },
    'Cancel': { hi: 'रद्द करें', pt: 'Cancelar', ru: 'Отмена' },
    'Propose a project': { hi: 'परियोजना प्रस्तावित करें', pt: 'Propor um projeto', ru: 'Предложить проект' },
    'No suitable existing project? Start one and link it to this demand — you can update its status and outcome later from Projects & outcomes.':
      { hi: 'कोई उपयुक्त मौजूदा परियोजना नहीं? एक शुरू करें और इस माँग से जोड़ें — इसकी स्थिति और परिणाम बाद में "परियोजनाएँ और परिणाम" से अपडेट कर सकते हैं।',
        pt: 'Nenhum projeto existente adequado? Crie um e vincule-o a esta demanda — você pode atualizar o status e o resultado depois em Projetos e resultados.',
        ru: 'Нет подходящего проекта? Создайте его и привяжите к запросу — статус и результат можно обновить позже в разделе «Проекты и результаты».' },
    'Project name': { hi: 'परियोजना का नाम', pt: 'Nome do projeto', ru: 'Название проекта' },
    'Expected completion (optional)': { hi: 'अपेक्षित पूर्णता (वैकल्पिक)', pt: 'Conclusão prevista (opcional)', ru: 'Ожидаемое завершение (необязательно)' },
    'Create & link project': { hi: 'परियोजना बनाएँ और जोड़ें', pt: 'Criar e vincular projeto', ru: 'Создать и привязать проект' },

    /* ---------- evidence detail ---------- */
    'Back to dashboard': { hi: 'डैशबोर्ड पर वापस', pt: 'Voltar ao painel', ru: 'Назад к панели' },
    'This is a plain-English evidence summary for one collective demand — everything below is computed fresh from citizen reports and official data, not opinion.':
      { hi: 'यह एक सामूहिक माँग का सरल-भाषा साक्ष्य सारांश है — नीचे सब कुछ नागरिक रिपोर्टों और आधिकारिक डेटा से नए सिरे से निकाला गया है, राय नहीं।',
        pt: 'Este é um resumo de evidências em linguagem simples para uma demanda coletiva — tudo abaixo é calculado a partir de relatos de cidadãos e dados oficiais, não de opinião.',
        ru: 'Это сводка доказательств простым языком по одному коллективному запросу — всё ниже рассчитано из обращений граждан и официальных данных, а не мнений.' },
    'Demand — how many people, how strongly': { hi: 'माँग — कितने लोग, कितनी तीव्रता से', pt: 'Demanda — quantas pessoas, com que intensidade', ru: 'Запрос — сколько людей и насколько остро' },
    "Infrastructure — what's already there": { hi: 'बुनियादी ढाँचा — जो पहले से मौजूद है', pt: 'Infraestrutura — o que já existe', ru: 'Инфраструктура — что уже есть' },
    'Population affected': { hi: 'प्रभावित आबादी', pt: 'População afetada', ru: 'Затронутое население' },
    'Population data unavailable for this area': { hi: 'इस क्षेत्र के लिए आबादी डेटा उपलब्ध नहीं', pt: 'Dados de população indisponíveis para esta área', ru: 'Данных о населении для этого района нет' },
    'Existing investment — is this already being addressed?': { hi: 'मौजूदा निवेश — क्या इस पर पहले से काम हो रहा है?', pt: 'Investimento existente — isto já está sendo tratado?', ru: 'Существующие инвестиции — этим уже занимаются?' },
    'How confident is this evidence?': { hi: 'यह साक्ष्य कितना भरोसेमंद है?', pt: 'Qual a confiança desta evidência?', ru: 'Насколько надёжны эти доказательства?' },
    'Why this was flagged to you': { hi: 'यह आपको क्यों दिखाया गया', pt: 'Por que isto foi sinalizado para você', ru: 'Почему это отмечено для вас' },
    'Recommended next step:': { hi: 'सुझाया गया अगला कदम:', pt: 'Próximo passo recomendado:', ru: 'Рекомендуемый следующий шаг:' },
    'Decision recorded:': { hi: 'निर्णय दर्ज:', pt: 'Decisão registrada:', ru: 'Решение зафиксировано:' },
    'Official status:': { hi: 'आधिकारिक स्थिति:', pt: 'Status oficial:', ru: 'Официальный статус:' },
    'Coverage:': { hi: 'कवरेज:', pt: 'Cobertura:', ru: 'Покрытие:' },
    'Unknown': { hi: 'अज्ञात', pt: 'Desconhecido', ru: 'Неизвестно' },
    'Data freshness:': { hi: 'डेटा नवीनता:', pt: 'Atualidade dos dados:', ru: 'Актуальность данных:' },
    'Severity:': { hi: 'गंभीरता:', pt: 'Gravidade:', ru: 'Серьёзность:' },
    'Review & decide': { hi: 'समीक्षा करें और निर्णय लें', pt: 'Analisar e decidir', ru: 'Изучить и решить' },
    'Update decision': { hi: 'निर्णय अपडेट करें', pt: 'Atualizar decisão', ru: 'Обновить решение' },

    /* ---------- projects & outcomes ---------- */
    'Projects & outcomes': { hi: 'परियोजनाएँ और परिणाम', pt: 'Projetos e resultados', ru: 'Проекты и результаты' },
    'Every intervention linked to a citizen demand, its implementation status, and whether the outcome has been verified against the ground.':
      { hi: 'नागरिक माँग से जुड़ा हर हस्तक्षेप, उसकी क्रियान्वयन स्थिति, और परिणाम ज़मीन पर सत्यापित हुआ या नहीं।',
        pt: 'Cada intervenção vinculada a uma demanda de cidadão, seu status de execução e se o resultado foi verificado no terreno.',
        ru: 'Каждая мера, связанная с запросом граждан, её статус реализации и подтверждён ли результат на месте.' },
    'Total projects': { hi: 'कुल परियोजनाएँ', pt: 'Total de projetos', ru: 'Всего проектов' },
    'In progress': { hi: 'प्रगति पर', pt: 'Em andamento', ru: 'В процессе' },
    'Completed': { hi: 'पूर्ण', pt: 'Concluído', ru: 'Завершено' },
    'Verified outcomes': { hi: 'सत्यापित परिणाम', pt: 'Resultados verificados', ru: 'Подтверждённые результаты' },
    "No projects linked yet — propose one from any demand's evidence page once you've reviewed it.":
      { hi: 'अभी कोई परियोजना नहीं जुड़ी — किसी माँग की समीक्षा के बाद उसके साक्ष्य पृष्ठ से एक प्रस्तावित करें।',
        pt: 'Ainda não há projetos vinculados — proponha um a partir da página de evidências de qualquer demanda depois de analisá-la.',
        ru: 'Проекты ещё не привязаны — предложите его со страницы доказательств запроса после изучения.' },
    'Planning': { hi: 'योजना', pt: 'Planejamento', ru: 'Планирование' },
    'Approval': { hi: 'अनुमोदन', pt: 'Aprovação', ru: 'Согласование' },
    'Tender': { hi: 'निविदा', pt: 'Licitação', ru: 'Тендер' },
    'Construction': { hi: 'निर्माण', pt: 'Construção', ru: 'Строительство' },
    'Completion': { hi: 'समापन', pt: 'Conclusão', ru: 'Завершение' },
    'Linked demand:': { hi: 'जुड़ी माँग:', pt: 'Demanda vinculada:', ru: 'Связанный запрос:' },
    'View evidence': { hi: 'साक्ष्य देखें', pt: 'Ver evidência', ru: 'Смотреть доказательства' },
    'Update': { hi: 'अपडेट', pt: 'Atualizar', ru: 'Обновить' },
    'Status': { hi: 'स्थिति', pt: 'Status', ru: 'Статус' },
    'Add a milestone (optional)': { hi: 'एक पड़ाव जोड़ें (वैकल्पिक)', pt: 'Adicionar um marco (opcional)', ru: 'Добавить веху (необязательно)' },
    'Update status': { hi: 'स्थिति अपडेट करें', pt: 'Atualizar status', ru: 'Обновить статус' },
    'Before indicator': { hi: 'पहले का संकेतक', pt: 'Indicador antes', ru: 'Показатель «до»' },
    'After indicator': { hi: 'बाद का संकेतक', pt: 'Indicador depois', ru: 'Показатель «после»' },
    'Impact % (optional)': { hi: 'प्रभाव % (वैकल्पिक)', pt: 'Impacto % (opcional)', ru: 'Влияние, % (необязательно)' },
    'Confirm this outcome is verified': { hi: 'पुष्टि करें कि यह परिणाम सत्यापित है', pt: 'Confirmar que este resultado foi verificado', ru: 'Подтвердить, что результат проверен' },
    'Record outcome': { hi: 'परिणाम दर्ज करें', pt: 'Registrar resultado', ru: 'Зафиксировать результат' },

    /* ---------- maps ---------- */
    'Community demand map': { hi: 'सामुदायिक माँग मानचित्र', pt: 'Mapa de demandas da comunidade', ru: 'Карта запросов сообщества' },
    'Demand intelligence': { hi: 'माँग विश्लेषण', pt: 'Inteligência de demanda', ru: 'Аналитика запросов' },
    'Categories': { hi: 'श्रेणियाँ', pt: 'Categorias', ru: 'Категории' },
    'All issues': { hi: 'सभी समस्याएँ', pt: 'Todos os problemas', ru: 'Все проблемы' },
    'Active near you': { hi: 'आपके पास सक्रिय', pt: 'Ativo perto de você', ru: 'Активно рядом' },
    'Active clusters': { hi: 'सक्रिय समूह', pt: 'Grupos ativos', ru: 'Активные группы' },
    'Loading…': { hi: 'लोड हो रहा है…', pt: 'Carregando…', ru: 'Загрузка…' },
    'No active issues in this category yet.': { hi: 'इस श्रेणी में अभी कोई सक्रिय समस्या नहीं।', pt: 'Ainda não há problemas ativos nesta categoria.', ru: 'В этой категории пока нет активных проблем.' },
    'No active clusters in this category yet.': { hi: 'इस श्रेणी में अभी कोई सक्रिय समूह नहीं।', pt: 'Ainda não há grupos ativos nesta categoria.', ru: 'В этой категории пока нет активных групп.' },
    'Location pending': { hi: 'स्थान लंबित', pt: 'Localização pendente', ru: 'Местоположение уточняется' },
    'Review evidence →': { hi: 'साक्ष्य की समीक्षा करें →', pt: 'Analisar evidência →', ru: 'Изучить доказательства →' },

    /* ---------- categories (from DB) ---------- */
    'Healthcare Access': { hi: 'स्वास्थ्य सेवा पहुँच', pt: 'Acesso à saúde', ru: 'Доступ к здравоохранению' },
    'Water & Sanitation': { hi: 'जल और स्वच्छता', pt: 'Água e saneamento', ru: 'Вода и санитария' },
    'Roads & Transport': { hi: 'सड़क और परिवहन', pt: 'Estradas e transporte', ru: 'Дороги и транспорт' },
    'Electricity & Utilities': { hi: 'बिजली और सुविधाएँ', pt: 'Eletricidade e serviços públicos', ru: 'Электричество и коммунальные услуги' },
    'Education Access': { hi: 'शिक्षा पहुँच', pt: 'Acesso à educação', ru: 'Доступ к образованию' },
    'Waste / Drainage / Public Environment': { hi: 'कचरा / जल निकासी / सार्वजनिक पर्यावरण', pt: 'Resíduos / Drenagem / Ambiente público', ru: 'Отходы / дренаж / общественная среда' },

    /* ---------- admin ---------- */
    'Platform configuration': { hi: 'प्लेटफ़ॉर्म कॉन्फ़िगरेशन', pt: 'Configuração da plataforma', ru: 'Настройки платформы' },
    'Technical configuration only — no policy authority.': { hi: 'केवल तकनीकी कॉन्फ़िगरेशन — कोई नीति अधिकार नहीं।', pt: 'Apenas configuração técnica — sem autoridade sobre políticas.', ru: 'Только техническая настройка — без полномочий по политике.' },
    'Countries': { hi: 'देश', pt: 'Países', ru: 'Страны' },
    'Code': { hi: 'कोड', pt: 'Código', ru: 'Код' },
    'Name': { hi: 'नाम', pt: 'Nome', ru: 'Имя' },
    'Languages': { hi: 'भाषाएँ', pt: 'Idiomas', ru: 'Языки' },
    'Demo actors': { hi: 'डेमो अभिनेता', pt: 'Atores de demonstração', ru: 'Демо-профили' },
    'ID': { hi: 'आईडी', pt: 'ID', ru: 'ID' },
    'Role': { hi: 'भूमिका', pt: 'Função', ru: 'Роль' },
    'Country': { hi: 'देश', pt: 'País', ru: 'Страна' },

    /* ---------- errors ---------- */
    'Page not found': { hi: 'पृष्ठ नहीं मिला', pt: 'Página não encontrada', ru: 'Страница не найдена' },
    "The page you're looking for doesn't exist.": { hi: 'आप जो पृष्ठ ढूँढ रहे हैं वह मौजूद नहीं है।', pt: 'A página que você procura não existe.', ru: 'Запрашиваемая страница не существует.' },
    'Go home': { hi: 'होम पर जाएँ', pt: 'Ir para o início', ru: 'На главную' },
    'Something went wrong': { hi: 'कुछ गड़बड़ हो गई', pt: 'Algo deu errado', ru: 'Что-то пошло не так' },
    'An unexpected error occurred. The team has been notified.': { hi: 'एक अप्रत्याशित त्रुटि हुई। टीम को सूचित कर दिया गया है।', pt: 'Ocorreu um erro inesperado. A equipe foi notificada.', ru: 'Произошла непредвиденная ошибка. Команда уведомлена.' },

    /* ---------- flash messages ---------- */
    'Please select a demo account to continue.': { hi: 'जारी रखने के लिए कृपया एक डेमो खाता चुनें।', pt: 'Selecione uma conta de demonstração para continuar.', ru: 'Выберите демо-аккаунт, чтобы продолжить.' },
    'No audio received. Please try again.': { hi: 'कोई ऑडियो नहीं मिला। कृपया फिर से प्रयास करें।', pt: 'Nenhum áudio recebido. Tente novamente.', ru: 'Аудио не получено. Попробуйте ещё раз.' },
    "Please describe your community's need.": { hi: 'कृपया अपने समुदाय की ज़रूरत बताएँ।', pt: 'Descreva a necessidade da sua comunidade.', ru: 'Опишите нужду вашего сообщества.' },
    'Attach a photo to add evidence, or use Join instead.': { hi: 'साक्ष्य जोड़ने के लिए फ़ोटो लगाएँ, या इसके बजाय "जुड़ें" का उपयोग करें।', pt: 'Anexe uma foto para adicionar evidência, ou use Juntar-se.', ru: 'Прикрепите фото, чтобы добавить доказательство, или используйте «Присоединиться».' },
    'Unknown action.': { hi: 'अज्ञात क्रिया।', pt: 'Ação desconhecida.', ru: 'Неизвестное действие.' },
    'Select a citizen account to view your timeline.': { hi: 'अपनी टाइमलाइन देखने के लिए एक नागरिक खाता चुनें।', pt: 'Selecione uma conta de cidadão para ver a sua linha do tempo.', ru: 'Выберите аккаунт гражданина, чтобы увидеть хронику.' },
    'Invalid verification state.': { hi: 'अमान्य सत्यापन स्थिति।', pt: 'Estado de verificação inválido.', ru: 'Недопустимое состояние проверки.' },
    'Thanks for the update.': { hi: 'अपडेट के लिए धन्यवाद।', pt: 'Obrigado pela atualização.', ru: 'Спасибо за обновление.' },
    "You already reported this — here's where it stands.": { hi: 'आपने यह पहले ही दर्ज कर दिया है — यह कहाँ है, देखिए।', pt: 'Você já relatou isto — veja em que pé está.', ru: 'Вы уже сообщали об этом — вот текущий статус.' },
    'A reason is required for every decision.': { hi: 'हर निर्णय के लिए कारण आवश्यक है।', pt: 'É obrigatório um motivo para cada decisão.', ru: 'Для каждого решения нужна причина.' },
    'Invalid decision type for your role.': { hi: 'आपकी भूमिका के लिए अमान्य निर्णय प्रकार।', pt: 'Tipo de decisão inválido para a sua função.', ru: 'Недопустимый тип решения для вашей роли.' },
    'Decision recorded.': { hi: 'निर्णय दर्ज किया गया।', pt: 'Decisão registrada.', ru: 'Решение зафиксировано.' },
    'A project name is required.': { hi: 'परियोजना का नाम आवश्यक है।', pt: 'É obrigatório um nome de projeto.', ru: 'Требуется название проекта.' },
    'Invalid project status.': { hi: 'अमान्य परियोजना स्थिति।', pt: 'Status de projeto inválido.', ru: 'Недопустимый статус проекта.' },
    'Project status updated.': { hi: 'परियोजना स्थिति अपडेट की गई।', pt: 'Status do projeto atualizado.', ru: 'Статус проекта обновлён.' },
    'Outcome recorded.': { hi: 'परिणाम दर्ज किया गया।', pt: 'Resultado registrado.', ru: 'Результат зафиксирован.' },
    'Unknown actor — please select one from the list.': { hi: 'अज्ञात अभिनेता — कृपया सूची में से एक चुनें।', pt: 'Ator desconhecido — selecione um da lista.', ru: 'Неизвестный профиль — выберите один из списка.' }
  };

  /* ---- rich-markup blocks: [data-i18n-html="key"] ----------------- */
  var HTML_DICT = {
    'hero.title': {
      en: 'From<br><span class="accent">collective demand</span><br>to visible outcome.',
      hi: '<span class="accent">सामूहिक माँग</span> से<br>दिखते परिणाम तक।',
      pt: 'Da <span class="accent">demanda coletiva</span><br>ao resultado visível.',
      ru: 'От <span class="accent">коллективного запроса</span><br>к видимому результату.'
    }
  };

  /* ---- interpolated-string rules (ordered) ------------------------- */
  var RULES = [
    { re: /^(\d+)\s+people$/,
      hi: '$1 लोग', pt: '$1 pessoas', ru: '$1 чел.' },
    { re: /^(\d+)\s+reports$/,
      hi: '$1 रिपोर्टें', pt: '$1 relatos', ru: '$1 обращений' },
    { re: /^(\d+)\s+unique contributors$/,
      hi: '$1 अलग-अलग योगदानकर्ता', pt: '$1 contribuintes únicos', ru: '$1 уникальных участников' },
    { re: /^(\d+)%\s+match$/,
      hi: '$1% मेल', pt: '$1% de correspondência', ru: '$1% совпадение' },
    { re: /^(\d+)%\s+still affected$/,
      hi: '$1% अब भी प्रभावित', pt: '$1% ainda afetados', ru: '$1% всё ещё затронуты' },
    { re: /^(\d+(?:\.\d+)?)%\s+improvement$/,
      hi: '$1% सुधार', pt: '$1% de melhoria', ru: 'улучшение на $1%' },
    { re: /^Demo:\s+(.+)$/,
      hi: 'डेमो: $1', pt: 'Demonstração: $1', ru: 'Демо: $1' },
    { re: /^(Critical|High|Medium|Low|CRITICAL|HIGH|MEDIUM|LOW)\s+priority$/i,
      hi: function (m) { var k = cap(m[1]); return (DICT[k] ? DICT[k].hi : m[1]) + ' प्राथमिकता'; },
      pt: function (m) { var k = cap(m[1]); return 'Prioridade ' + (DICT[k] ? DICT[k].pt.toLowerCase() : m[1]); },
      ru: function (m) { var k = cap(m[1]); return (DICT[k] ? DICT[k].ru : m[1]) + ' приоритет'; } },
    { re: /^(.+?)\s+·\s+ranked by real evidence — prioritise, defer, or reject$/,
      hi: '$1 · असली साक्ष्य के आधार पर क्रम — प्राथमिकता दें, स्थगित करें या अस्वीकार करें',
      pt: '$1 · ordenado por evidência real — priorize, adie ou rejeite',
      ru: '$1 · по реальным доказательствам — приоритет, отложить или отклонить' },
    { re: /^(.+?)\s+·\s+where investment is missing, and what's already under way$/,
      hi: '$1 · जहाँ निवेश नहीं है, और जो पहले से चल रहा है',
      pt: '$1 · onde falta investimento e o que já está em andamento',
      ru: '$1 · где нет инвестиций и что уже идёт' },
    { re: /^Step\s+(\d)\s+of\s+4\s+·\s+(.+)$/,
      hi: function (m) { return 'चरण ' + m[1] + ' / 4 · ' + tr(m[2], 'hi'); },
      pt: function (m) { return 'Etapa ' + m[1] + ' de 4 · ' + tr(m[2], 'pt'); },
      ru: function (m) { return 'Шаг ' + m[1] + ' из 4 · ' + tr(m[2], 'ru'); } },
    { re: /^Nearest facility:\s+([\d.]+)\s+km away$/,
      hi: 'निकटतम सुविधा: $1 किमी दूर', pt: 'Instalação mais próxima: $1 km de distância', ru: 'Ближайший объект: $1 км' },
    { re: /^Estimated\s+([\d,]+)\s+people live in the affected area$/,
      hi: 'अनुमानित $1 लोग प्रभावित क्षेत्र में रहते हैं', pt: 'Estimados $1 habitantes na área afetada', ru: 'Примерно $1 чел. живёт в затронутой зоне' },
    { re: /^Every pin is a collective demand cluster in (.+?), not an individual complaint — filter by category, or click a pin for details\.$/,
      hi: 'हर पिन $1 में एक सामूहिक माँग समूह है, कोई व्यक्तिगत शिकायत नहीं — श्रेणी से छाँटें, या विवरण के लिए पिन पर क्लिक करें।',
      pt: 'Cada marcador é um agrupamento de demanda coletiva em $1, não uma reclamação individual — filtre por categoria ou clique em um marcador para detalhes.',
      ru: 'Каждая метка — это кластер коллективного запроса в $1, а не отдельная жалоба — фильтруйте по категории или нажмите на метку для подробностей.' },
    { re: /^Every pin is a collective demand cluster in (.+?) — click one to open its evidence card\.$/,
      hi: 'हर पिन $1 में एक सामूहिक माँग समूह है — साक्ष्य कार्ड खोलने के लिए किसी एक पर क्लिक करें।',
      pt: 'Cada marcador é um agrupamento de demanda coletiva em $1 — clique em um para abrir seu cartão de evidências.',
      ru: 'Каждая метка — это кластер коллективного запроса в $1 — нажмите на неё, чтобы открыть карточку доказательств.' },
    { re: /^You've (joined|confirmed) this community issue\.$/,
      hi: function (m) { return 'आप इस सामुदायिक समस्या से ' + (m[1] === 'joined' ? 'जुड़ गए' : 'ने पुष्टि की') + '।'; },
      pt: function (m) { return 'Você ' + (m[1] === 'joined' ? 'juntou-se a' : 'confirmou') + ' este problema da comunidade.'; },
      ru: function (m) { return m[1] === 'joined' ? 'Вы присоединились к этой проблеме сообщества.' : 'Вы подтвердили эту проблему сообщества.'; } }
  ];

  /* ---- <title> handling ------------------------------------------- */
  var TITLE_SUFFIXES = [' — BRICS People First', ' — NEVO'];
  // page-title heads not already covered as body strings
  var TITLE_DICT = {
    'Community Issues': { hi: 'सामुदायिक समस्याएँ', pt: 'Problemas da comunidade', ru: 'Проблемы сообщества' },
    'Your report': { hi: 'आपकी रिपोर्ट', pt: 'Seu relato', ru: 'Ваше обращение' },
    'Evidence': { hi: 'साक्ष्य', pt: 'Evidência', ru: 'Доказательства' },
    'Projects & Outcomes': { hi: 'परियोजनाएँ और परिणाम', pt: 'Projetos e resultados', ru: 'Проекты и результаты' },
    'Community Demand Map': { hi: 'सामुदायिक माँग मानचित्र', pt: 'Mapa de demandas da comunidade', ru: 'Карта запросов сообщества' },
    'Demand Intelligence Map': { hi: 'माँग विश्लेषण मानचित्र', pt: 'Mapa de inteligência de demanda', ru: 'Карта аналитики запросов' },
    'Page not found': DICT['Page not found'],
    'Something went wrong': DICT['Something went wrong']
  };

  /* ---- engine ---------------------------------------------------- */
  var SKIP_TAGS = { SCRIPT: 1, STYLE: 1, TEXTAREA: 1, NOSCRIPT: 1, CODE: 1, PRE: 1 };
  var ATTRS = ['placeholder', 'title', 'aria-label', 'alt'];
  var current = DEFAULT;
  var observer = null;

  function norm(s) { return s.replace(/\s+/g, ' ').trim(); }
  function cap(s) { return s.charAt(0).toUpperCase() + s.slice(1).toLowerCase(); }

  function applyRule(rule, key, lang) {
    var m = key.match(rule.re);
    if (!m) return null;
    var repl = rule[lang];
    if (repl == null) return null;
    if (typeof repl === 'function') return repl(m);
    return key.replace(rule.re, repl);
  }

  // core translate: normalised English -> string in `lang`, or null if unknown
  function tr(text, lang) {
    if (lang === 'en') return null;
    var key = norm(text);
    if (!key) return null;
    var hit = DICT[key];
    if (hit && hit[lang] != null) return hit[lang];
    for (var i = 0; i < RULES.length; i++) {
      var out = applyRule(RULES[i], key, lang);
      if (out != null) return out;
    }
    return null;
  }

  // translate preserving the original leading / trailing whitespace
  function trEdge(text, lang) {
    var core = tr(text, lang);
    if (core == null) return null;
    var lead = (text.match(/^\s*/) || [''])[0];
    var trail = (text.match(/\s*$/) || [''])[0];
    return lead + core + trail;
  }

  function skip(node) {
    for (var el = node.parentNode; el && el.nodeType === 1; el = el.parentNode) {
      if (SKIP_TAGS[el.tagName]) return true;
      if (el.hasAttribute('data-no-i18n') || el.isContentEditable) return true;
    }
    return false;
  }

  function walkText(root, lang) {
    var walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, null);
    var batch = [];
    var n;
    while ((n = walker.nextNode())) {
      if (!n.nodeValue || !n.nodeValue.trim()) continue;
      if (skip(n)) continue;
      batch.push(n);
    }
    batch.forEach(function (node) {
      if (node.__i18nOrig == null) node.__i18nOrig = node.nodeValue;
      var out = trEdge(node.__i18nOrig, lang);
      var next = out != null ? out : node.__i18nOrig;
      if (node.nodeValue !== next) node.nodeValue = next;
    });
  }

  function walkAttrs(root, lang) {
    var sel = ATTRS.map(function (a) { return '[' + a + ']'; }).join(',') +
              ',input[type=submit],input[type=button]';
    var els = root.querySelectorAll ? root.querySelectorAll(sel) : [];
    Array.prototype.forEach.call(els, function (el) {
      if (skip(el)) return;
      if (!el.__i18nAttr) el.__i18nAttr = {};
      ATTRS.forEach(function (a) {
        if (!el.hasAttribute(a)) return;
        if (el.__i18nAttr[a] == null) el.__i18nAttr[a] = el.getAttribute(a);
        var out = tr(el.__i18nAttr[a], lang);
        el.setAttribute(a, out != null ? out : el.__i18nAttr[a]);
      });
      if ((el.tagName === 'INPUT') && (el.type === 'submit' || el.type === 'button')) {
        if (el.__i18nAttr.value == null) el.__i18nAttr.value = el.value;
        var v = tr(el.__i18nAttr.value, lang);
        el.value = v != null ? v : el.__i18nAttr.value;
      }
    });
  }

  function walkHtml(root, lang) {
    var els = root.querySelectorAll ? root.querySelectorAll('[data-i18n-html]') : [];
    Array.prototype.forEach.call(els, function (el) {
      var key = el.getAttribute('data-i18n-html');
      var entry = HTML_DICT[key];
      if (!entry) return;
      var next = entry[lang] || entry.en;
      if (next != null && el.innerHTML !== next) el.innerHTML = next;
    });
  }

  function applyTitle(lang) {
    if (document.__titleOrig == null) document.__titleOrig = document.title;
    var t = document.__titleOrig;
    if (lang === 'en') { document.title = t; return; }
    for (var i = 0; i < TITLE_SUFFIXES.length; i++) {
      var suf = TITLE_SUFFIXES[i];
      var idx = t.indexOf(suf);
      if (idx > -1) {
        var head = t.slice(0, idx);
        var td = TITLE_DICT[head];
        var htr = (td && td[lang]) || tr(head, lang);
        document.title = (htr != null ? htr : head) + suf;
        return;
      }
    }
    var full = tr(t, lang);
    document.title = full != null ? full : t;
  }

  function apply(lang) {
    current = lang;
    document.documentElement.lang = lang;
    if (observer) observer.disconnect();
    walkHtml(document.body, lang);
    walkText(document.body, lang);
    walkAttrs(document.body, lang);
    applyTitle(lang);
    if (observer) observer.observe(document.body, { childList: true, subtree: true });
    document.dispatchEvent(new CustomEvent('nevo:langchange', { detail: { lang: lang } }));
  }

  function store(lang) {
    try { localStorage.setItem(STORAGE_KEY, lang); } catch (e) {}
    try {
      document.cookie = STORAGE_KEY + '=' + lang + ';path=/;max-age=31536000;samesite=lax';
    } catch (e) {}
  }

  function read() {
    try {
      var v = localStorage.getItem(STORAGE_KEY);
      if (v && LANGS[v]) return v;
    } catch (e) {}
    var m = document.cookie.match(/(?:^|;\s*)nevo_lang=(\w+)/);
    if (m && LANGS[m[1]]) return m[1];
    return DEFAULT;
  }

  function setLang(lang) {
    if (!LANGS[lang]) lang = DEFAULT;
    store(lang);
    apply(lang);
    var sel = document.getElementById('lang-switch');
    if (sel && sel.value !== lang) sel.value = lang;
  }

  function initObserver() {
    if (!window.MutationObserver) return;
    var pending = [];
    var scheduled = false;
    function flush() {
      scheduled = false;
      var nodes = pending.splice(0);
      if (current === DEFAULT) return;
      observer.disconnect();
      nodes.forEach(function (node) {
        if (node.nodeType === 3) {
          if (node.nodeValue && node.nodeValue.trim() && !skip(node)) {
            if (node.__i18nOrig == null) node.__i18nOrig = node.nodeValue;
            var out = trEdge(node.__i18nOrig, current);
            if (out != null && node.nodeValue !== out) node.nodeValue = out;
          }
        } else if (node.nodeType === 1) {
          walkText(node, current);
          walkAttrs(node, current);
        }
      });
      observer.observe(document.body, { childList: true, subtree: true });
    }
    observer = new MutationObserver(function (muts) {
      muts.forEach(function (mut) {
        Array.prototype.forEach.call(mut.addedNodes, function (n) { pending.push(n); });
      });
      if (pending.length && !scheduled) { scheduled = true; setTimeout(flush, 60); }
    });
  }

  function initSwitcher() {
    var sel = document.getElementById('lang-switch');
    if (!sel) return;
    sel.value = current;
    sel.addEventListener('change', function () { setLang(sel.value); });
  }

  function boot() {
    current = read();
    initObserver();
    initSwitcher();
    if (current !== DEFAULT) apply(current);
    else document.documentElement.lang = 'en';
  }

  window.NEVO_I18N = {
    langs: LANGS,
    get: function () { return current; },
    set: setLang,
    t: function (text) {
      var out = tr(text, current);
      return out != null ? out : text;
    }
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
