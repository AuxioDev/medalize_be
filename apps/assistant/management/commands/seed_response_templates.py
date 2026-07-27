"""Seeds the initial ResponseTemplate bank.

DRAFT CONTENT — same caveat as the emergency/off-topic keyword lists in
service.py: this starter set was written for engineering integration, not
clinically reviewed. Have a doctor and a native speaker per language review
wording before relying on it in production. Safe to re-run — matches
existing templates by their English answer text and updates them in place
instead of creating duplicates.
"""
from django.core.management.base import BaseCommand

from apps.assistant.models import ResponseTemplate

# Each entry: specialization code (or '' for general, non-referral topics)
# plus per-language triggers (a handful of phrasings for the fuzzy matcher)
# and answers (single paragraph, no disclaimer — service.py appends that).
TEMPLATES = [
    {
        'specialization': 'general_practice',
        'triggers': {
            'en': ['headache', 'my head hurts', 'head pain', 'migraine'],
            'ru': ['головная боль', 'болит голова', 'мигрень'],
            'az': ['baş ağrısı', 'başım ağrıyır', 'miqren'],
            'tr': ['baş ağrısı', 'başım ağrıyor', 'migren'],
            'fr': ['mal de tête', "j'ai mal à la tête", 'migraine'],
            'zh': ['头痛', '头疼', '偏头痛'],
        },
        'answers': {
            'en': 'Headaches are common and often caused by stress, dehydration, lack of sleep, or eye strain. Resting, drinking water, and avoiding screens can help with mild cases. If the headache is severe, sudden, comes with other symptoms, or keeps recurring, please see a doctor.',
            'ru': 'Головная боль — распространённое явление, часто вызванное стрессом, обезвоживанием, недосыпом или напряжением глаз. При лёгких случаях помогает отдых, вода и отказ от экранов. Если боль сильная, внезапная, сопровождается другими симптомами или повторяется часто, обратитесь к врачу.',
            'az': 'Baş ağrısı geniş yayılmışdır və çox vaxt stress, dehidratasiya, yuxusuzluq və ya göz gərginliyi ilə bağlıdır. Yüngül hallarda istirahət, su içmək və ekranlardan uzaqlaşmaq kömək edir. Ağrı güclü, qəfil, digər simptomlarla müşayiət olunursa və ya təkrarlanırsa, həkimə müraciət edin.',
            'tr': 'Baş ağrısı yaygındır ve genellikle stres, susuzluk, uykusuzluk veya göz yorgunluğundan kaynaklanır. Hafif durumlarda dinlenmek, su içmek ve ekranlardan uzak durmak yardımcı olabilir. Ağrı şiddetliyse, aniyse, başka belirtilerle birlikteyse veya tekrarlıyorsa lütfen bir doktora görünün.',
            'fr': "Les maux de tête sont fréquents et souvent causés par le stress, la déshydratation, le manque de sommeil ou la fatigue oculaire. Le repos, l'hydratation et une pause d'écran peuvent aider dans les cas légers. Si le mal de tête est sévère, soudain, accompagné d'autres symptômes ou récurrent, consultez un médecin.",
            'zh': '头痛很常见，通常由压力、脱水、睡眠不足或用眼过度引起。轻度情况下，休息、多喝水、减少看屏幕的时间会有帮助。如果头痛剧烈、突然发作、伴有其他症状或反复出现，请及时就医。',
        },
    },
    {
        'specialization': 'pulmonology',
        'triggers': {
            'en': ['cough', 'runny nose', 'common cold', 'i have a cold'],
            'ru': ['кашель', 'насморк', 'простуда', 'я простудился'],
            'az': ['öskürək', 'burun axması', 'soyuqdəymə'],
            'tr': ['öksürük', 'burun akıntısı', 'soğuk algınlığı'],
            'fr': ['toux', 'nez qui coule', 'rhume', "j'ai un rhume"],
            'zh': ['咳嗽', '流鼻涕', '感冒'],
        },
        'answers': {
            'en': 'A cough and runny nose are usually signs of a common cold, which typically clears up on its own within one to two weeks. Rest, fluids, and over-the-counter symptom relief can help. See a doctor if the cough lasts more than three weeks, you have a high fever, or you have difficulty breathing.',
            'ru': 'Кашель и насморк обычно являются признаками простуды, которая, как правило, проходит сама в течение одной-двух недель. Помогают отдых, обильное питьё и безрецептурные средства от симптомов. Обратитесь к врачу, если кашель длится дольше трёх недель, есть высокая температура или затруднено дыхание.',
            'az': 'Öskürək və burun axması adətən soyuqdəymənin əlamətləridir və bir-iki həftə ərzində özbaşına keçir. İstirahət, maye qəbulu və reseptsiz simptomatik dərmanlar kömək edə bilər. Öskürək üç həftədən çox davam edərsə, yüksək hərarət olarsa və ya nəfəs almaq çətinləşərsə həkimə müraciət edin.',
            'tr': 'Öksürük ve burun akıntısı genellikle soğuk algınlığının belirtileridir ve genellikle bir-iki hafta içinde kendiliğinden geçer. Dinlenmek, sıvı tüketmek ve reçetesiz semptom giderici ilaçlar yardımcı olabilir. Öksürük üç haftadan uzun sürerse, yüksek ateşiniz varsa veya nefes almakta zorlanıyorsanız bir doktora görünün.',
            'fr': "La toux et le nez qui coule sont généralement des signes de rhume, qui disparaît habituellement de lui-même en une à deux semaines. Le repos, l'hydratation et des médicaments en vente libre peuvent aider. Consultez un médecin si la toux dure plus de trois semaines, en cas de forte fièvre ou de difficulté à respirer.",
            'zh': '咳嗽和流鼻涕通常是普通感冒的表现，一般一到两周内会自行好转。休息、多喝水和非处方药可以缓解症状。如果咳嗽持续超过三周、出现高烧或呼吸困难，请及时就医。',
        },
    },
    {
        'specialization': 'ent',
        'triggers': {
            'en': ['sore throat', 'throat hurts', 'pain when swallowing'],
            'ru': ['болит горло', 'боль в горле', 'больно глотать'],
            'az': ['boğaz ağrısı', 'boğazım ağrıyır', 'udqunma çətinliyi'],
            'tr': ['boğaz ağrısı', 'boğazım ağrıyor', 'yutkunurken ağrı'],
            'fr': ['mal de gorge', 'la gorge me fait mal', 'douleur en avalant'],
            'zh': ['喉咙痛', '嗓子疼', '吞咽疼痛'],
        },
        'answers': {
            'en': 'A sore throat is often caused by a viral infection and usually improves within a few days with rest, fluids, and warm drinks. See a doctor if it lasts more than a week, is very severe, or comes with a high fever or difficulty swallowing.',
            'ru': 'Боль в горле часто вызвана вирусной инфекцией и обычно проходит за несколько дней при отдыхе, обильном питье и тёплых напитках. Обратитесь к врачу, если боль длится больше недели, очень сильная или сопровождается высокой температурой либо затруднённым глотанием.',
            'az': 'Boğaz ağrısı çox vaxt virus infeksiyası ilə bağlıdır və istirahət, maye qəbulu və isti içkilərlə bir neçə gün ərzində yaxşılaşır. Ağrı bir həftədən çox davam edərsə, çox güclüdürsə və ya yüksək hərarət ya da udqunma çətinliyi ilə müşayiət olunursa həkimə müraciət edin.',
            'tr': 'Boğaz ağrısı genellikle viral bir enfeksiyondan kaynaklanır ve dinlenme, sıvı tüketimi ve ılık içeceklerle birkaç gün içinde iyileşir. Bir haftadan uzun sürerse, çok şiddetliyse veya yüksek ateş ya da yutma güçlüğü ile birlikteyse bir doktora görünün.',
            'fr': "Un mal de gorge est souvent causé par une infection virale et s'améliore généralement en quelques jours avec du repos, de l'hydratation et des boissons chaudes. Consultez un médecin s'il dure plus d'une semaine, est très sévère, ou s'accompagne d'une forte fièvre ou de difficultés à avaler.",
            'zh': '喉咙痛通常由病毒感染引起，通过休息、多喝水和温热饮品，一般几天内会好转。如果持续超过一周、非常严重，或伴有高烧、吞咽困难，请及时就医。',
        },
    },
    {
        'specialization': 'gastroenterology',
        'triggers': {
            'en': ['stomach ache', 'stomach pain', 'indigestion', 'my stomach hurts'],
            'ru': ['болит живот', 'боль в животе', 'расстройство желудка'],
            'az': ['qarın ağrısı', 'qarnım ağrıyır', 'həzm pozğunluğu'],
            'tr': ['karın ağrısı', 'karnım ağrıyor', 'hazımsızlık'],
            'fr': ['mal au ventre', "j'ai mal au ventre", 'indigestion'],
            'zh': ['胃痛', '肚子疼', '消化不良'],
        },
        'answers': {
            'en': 'Mild stomach pain is often related to diet, stress, or indigestion and tends to pass within a day or two. Eating smaller meals and avoiding spicy or fatty food can help. See a doctor if the pain is severe, persistent, or accompanied by vomiting, fever, or blood.',
            'ru': 'Лёгкая боль в животе часто связана с питанием, стрессом или расстройством желудка и обычно проходит за день-два. Помогает есть небольшими порциями и избегать острой и жирной пищи. Обратитесь к врачу, если боль сильная, не проходит или сопровождается рвотой, температурой или кровью.',
            'az': 'Yüngül qarın ağrısı çox vaxt qidalanma, stress və ya həzm pozğunluğu ilə bağlıdır və bir-iki gün ərzində keçir. Az-az yemək və acı, yağlı qidalardan çəkinmək kömək edir. Ağrı güclüdürsə, keçmirsə və ya qusma, hərarət, qanaxma ilə müşayiət olunursa həkimə müraciət edin.',
            'tr': 'Hafif karın ağrısı genellikle beslenme, stres veya hazımsızlıkla ilgilidir ve bir-iki gün içinde geçer. Az ve sık yemek yemek, baharatlı veya yağlı yiyeceklerden kaçınmak yardımcı olabilir. Ağrı şiddetliyse, geçmiyorsa veya kusma, ateş ya da kanama ile birlikteyse bir doktora görünün.',
            'fr': "Une douleur légère à l'estomac est souvent liée à l'alimentation, au stress ou à une indigestion et disparaît généralement en un jour ou deux. Manger de plus petites portions et éviter les aliments épicés ou gras peut aider. Consultez un médecin si la douleur est sévère, persiste, ou s'accompagne de vomissements, de fièvre ou de sang.",
            'zh': '轻度胃痛通常与饮食、压力或消化不良有关，一到两天内会缓解。少食多餐、避免辛辣油腻食物有帮助。如果疼痛剧烈、持续不缓解，或伴有呕吐、发烧、便血，请及时就医。',
        },
    },
    {
        'specialization': 'dermatology',
        'triggers': {
            'en': ['skin rash', 'itchy skin', 'rash'],
            'ru': ['сыпь на коже', 'зуд кожи', 'высыпания'],
            'az': ['dəri səpgisi', 'dəri qaşınması', 'səpgi'],
            'tr': ['cilt döküntüsü', 'cildim kaşınıyor', 'döküntü'],
            'fr': ['éruption cutanée', 'peau qui démange', 'éruption'],
            'zh': ['皮疹', '皮肤瘙痒', '起疹子'],
        },
        'answers': {
            'en': 'Skin rashes can have many causes, including allergies, irritation, or minor infections, and many resolve on their own. Avoid scratching and keep the area clean. See a dermatologist if the rash spreads, is very painful, or does not improve within a few days.',
            'ru': 'Кожная сыпь может иметь много причин, включая аллергию, раздражение или лёгкую инфекцию, и часто проходит сама. Не расчёсывайте поражённый участок и держите его в чистоте. Обратитесь к дерматологу, если сыпь распространяется, очень болезненна или не проходит за несколько дней.',
            'az': 'Dəri səpgiləri allergiya, qıcıqlanma və ya yüngül infeksiya kimi bir çox səbəbdən yarana bilər və çox vaxt özbaşına keçir. Qaşımaqdan çəkinin və nahiyəni təmiz saxlayın. Səpgi yayılırsa, çox ağrılıdırsa və ya bir neçə gün ərzində yaxşılaşmırsa dermatoloqa müraciət edin.',
            'tr': 'Cilt döküntülerinin alerji, tahriş veya hafif enfeksiyon gibi birçok nedeni olabilir ve çoğu kendiliğinden geçer. Kaşımaktan kaçının ve bölgeyi temiz tutun. Döküntü yayılıyorsa, çok ağrılıysa veya birkaç gün içinde iyileşmiyorsa bir dermatoloğa görünün.',
            'fr': "Les éruptions cutanées peuvent avoir de nombreuses causes, notamment des allergies, une irritation ou une infection mineure, et beaucoup disparaissent d'elles-mêmes. Évitez de gratter et gardez la zone propre. Consultez un dermatologue si l'éruption s'étend, est très douloureuse, ou ne s'améliore pas en quelques jours.",
            'zh': '皮疹的原因很多，包括过敏、刺激或轻微感染，很多情况会自行消退。请避免抓挠，保持患处清洁。如果皮疹扩散、非常疼痛，或几天内没有好转，请就诊皮肤科医生。',
        },
    },
    {
        'specialization': 'orthopedics',
        'triggers': {
            'en': ['back pain', 'my back hurts'],
            'ru': ['боль в спине', 'болит спина'],
            'az': ['bel ağrısı', 'belim ağrıyır'],
            'tr': ['sırt ağrısı', 'sırtım ağrıyor', 'bel ağrısı'],
            'fr': ['mal de dos', "j'ai mal au dos"],
            'zh': ['背痛', '腰痛', '后背疼'],
        },
        'answers': {
            'en': 'Back pain is often caused by muscle strain and usually improves with rest, gentle movement, and over-the-counter pain relief. See a doctor if the pain is severe, spreads down your leg, or does not improve after a week.',
            'ru': 'Боль в спине часто вызвана растяжением мышц и обычно проходит при отдыхе, лёгких движениях и безрецептурных обезболивающих. Обратитесь к врачу, если боль сильная, отдаёт в ногу или не проходит спустя неделю.',
            'az': 'Bel ağrısı çox vaxt əzələ dartılması ilə bağlıdır və istirahət, yüngül hərəkət və reseptsiz ağrıkəsicilərlə yaxşılaşır. Ağrı güclüdürsə, ayağa yayılırsa və ya bir həftədən sonra keçmirsə həkimə müraciət edin.',
            'tr': 'Sırt ağrısı genellikle kas zorlanmasından kaynaklanır ve dinlenme, hafif hareket ve reçetesiz ağrı kesicilerle iyileşir. Ağrı şiddetliyse, bacağınıza yayılıyorsa veya bir hafta sonra geçmiyorsa bir doktora görünün.',
            'fr': "Le mal de dos est souvent causé par une tension musculaire et s'améliore généralement avec du repos, des mouvements doux et des antalgiques en vente libre. Consultez un médecin si la douleur est sévère, s'étend dans la jambe, ou ne s'améliore pas après une semaine.",
            'zh': '背痛通常由肌肉拉伤引起，通过休息、适度活动和非处方止痛药一般会好转。如果疼痛剧烈、放射到腿部，或一周后仍未改善，请及时就医。',
        },
    },
    {
        'specialization': 'orthopedics',
        'triggers': {
            'en': ['joint pain', 'knee pain', 'my joints hurt'],
            'ru': ['боль в суставах', 'болит колено', 'суставы болят'],
            'az': ['oynaq ağrısı', 'diz ağrısı', 'oynaqlarım ağrıyır'],
            'tr': ['eklem ağrısı', 'diz ağrısı', 'eklemlerim ağrıyor'],
            'fr': ['douleur articulaire', 'douleur au genou', 'mes articulations me font mal'],
            'zh': ['关节疼痛', '膝盖疼', '关节痛'],
        },
        'answers': {
            'en': 'Joint pain can result from overuse, minor injury, or inflammation. Rest and reducing strain on the joint often helps. See a doctor if the joint is swollen, red, very painful, or the pain does not improve within a few days.',
            'ru': 'Боль в суставах может возникать из-за перенапряжения, лёгкой травмы или воспаления. Часто помогает отдых и снижение нагрузки на сустав. Обратитесь к врачу, если сустав опух, покраснел, очень болезненный или боль не проходит за несколько дней.',
            'az': 'Oynaq ağrısı həddindən artıq yüklənmə, yüngül zədə və ya iltihabdan yarana bilər. İstirahət və oynağa düşən yükün azaldılması çox vaxt kömək edir. Oynaq şişibsə, qızarıbsa, çox ağrılıdırsa və ya bir neçə gün ərzində yaxşılaşmırsa həkimə müraciət edin.',
            'tr': 'Eklem ağrısı aşırı kullanım, hafif bir yaralanma veya iltihaptan kaynaklanabilir. Dinlenmek ve ekleme binen yükü azaltmak genellikle yardımcı olur. Eklem şişmişse, kızarmışsa, çok ağrılıysa veya birkaç gün içinde iyileşmiyorsa bir doktora görünün.',
            'fr': "La douleur articulaire peut résulter d'une sursollicitation, d'une blessure mineure ou d'une inflammation. Le repos et la réduction de la tension sur l'articulation aident souvent. Consultez un médecin si l'articulation est enflée, rouge, très douloureuse, ou si la douleur ne s'améliore pas en quelques jours.",
            'zh': '关节疼痛可能是由于过度使用、轻微损伤或炎症引起的。休息并减少关节负担通常有帮助。如果关节肿胀、发红、非常疼痛，或几天内没有改善，请及时就医。',
        },
    },
    {
        'specialization': 'general_practice',
        'triggers': {
            'en': ['fever', 'high temperature', 'i have a fever'],
            'ru': ['температура', 'высокая температура', 'у меня жар'],
            'az': ['hərarət', 'yüksək temperatur', 'atəşim var'],
            'tr': ['ateş', 'yüksek ateş', 'ateşim var'],
            'fr': ['fièvre', 'température élevée', "j'ai de la fièvre"],
            'zh': ['发烧', '发热', '体温高'],
        },
        'answers': {
            'en': 'A mild fever is usually a sign your body is fighting an infection. Rest, fluids, and fever-reducing medication can help. See a doctor if the fever is very high, lasts more than three days, or comes with severe symptoms.',
            'ru': 'Небольшая температура обычно означает, что организм борется с инфекцией. Помогают отдых, обильное питьё и жаропонижающие средства. Обратитесь к врачу, если температура очень высокая, держится больше трёх дней или сопровождается тяжёлыми симптомами.',
            'az': 'Yüngül hərarət adətən orqanizmin infeksiya ilə mübarizə apardığını göstərir. İstirahət, maye qəbulu və hərarət salan dərmanlar kömək edir. Hərarət çox yüksəkdirsə, üç gündən çox davam edirsə və ya ağır simptomlarla müşayiət olunursa həkimə müraciət edin.',
            'tr': 'Hafif ateş genellikle vücudunuzun bir enfeksiyonla savaştığının işaretidir. Dinlenme, sıvı tüketimi ve ateş düşürücü ilaçlar yardımcı olabilir. Ateş çok yüksekse, üç günden uzun sürerse veya şiddetli belirtilerle birlikteyse bir doktora görünün.',
            'fr': "Une légère fièvre est généralement le signe que votre corps combat une infection. Le repos, l'hydratation et des médicaments antipyrétiques peuvent aider. Consultez un médecin si la fièvre est très élevée, dure plus de trois jours, ou s'accompagne de symptômes sévères.",
            'zh': '轻度发烧通常说明身体正在对抗感染。休息、多喝水和退烧药可以帮助缓解。如果发烧很高、持续超过三天，或伴有严重症状，请及时就医。',
        },
    },
    {
        'specialization': 'general_practice',
        'triggers': {
            'en': ['fatigue', 'always tired', 'feeling tired', 'low energy'],
            'ru': ['усталость', 'постоянно устаю', 'нет сил'],
            'az': ['yorğunluq', 'daim yorğunam', 'enerjim yoxdur'],
            'tr': ['yorgunluk', 'sürekli yorgunum', 'enerjim yok'],
            'fr': ['fatigue', 'toujours fatigué', "manque d'énergie"],
            'zh': ['疲劳', '总是很累', '没有精神'],
        },
        'answers': {
            'en': 'Ongoing tiredness can be related to sleep, stress, diet, or an underlying condition. Try to keep a regular sleep schedule and stay hydrated. See a doctor if the fatigue is persistent, unexplained, or affects your daily life.',
            'ru': 'Постоянная усталость может быть связана со сном, стрессом, питанием или скрытым заболеванием. Старайтесь соблюдать режим сна и пить достаточно воды. Обратитесь к врачу, если усталость постоянная, необъяснимая или мешает повседневной жизни.',
            'az': 'Davamlı yorğunluq yuxu, stress, qidalanma və ya gizli bir vəziyyətlə bağlı ola bilər. Müntəzəm yuxu rejiminə əməl edin və kifayət qədər su için. Yorğunluq davamlıdırsa, izah edilə bilmirsə və ya gündəlik həyata təsir edirsə həkimə müraciət edin.',
            'tr': 'Sürekli yorgunluk uyku, stres, beslenme veya altta yatan bir durumla ilgili olabilir. Düzenli bir uyku düzeni sürdürmeye ve yeterli sıvı almaya çalışın. Yorgunluk kalıcıysa, açıklanamıyorsa veya günlük yaşamınızı etkiliyorsa bir doktora görünün.',
            'fr': "Une fatigue persistante peut être liée au sommeil, au stress, à l'alimentation ou à une condition sous-jacente. Essayez de garder un rythme de sommeil régulier et de bien vous hydrater. Consultez un médecin si la fatigue est persistante, inexpliquée, ou affecte votre vie quotidienne.",
            'zh': '持续的疲劳可能与睡眠、压力、饮食或潜在疾病有关。尽量保持规律的作息并充分补水。如果疲劳持续、原因不明，或影响日常生活，请及时就医。',
        },
    },
    {
        'specialization': 'psychiatry',
        'triggers': {
            'en': ['anxiety', 'feeling anxious', 'stressed', 'panic'],
            'ru': ['тревога', 'тревожность', 'стресс', 'паника'],
            'az': ['təşviş', 'narahatlıq', 'stress', 'panika'],
            'tr': ['kaygı', 'endişeliyim', 'stresli', 'panik'],
            'fr': ['anxiété', 'je me sens anxieux', 'stressé', 'panique'],
            'zh': ['焦虑', '感到焦虑', '压力大', '恐慌'],
        },
        'answers': {
            'en': 'Feeling anxious or stressed from time to time is common. Relaxation techniques, regular exercise, and talking to someone you trust can help. If anxiety is frequent, intense, or interferes with daily life, a mental health professional can offer support.',
            'ru': 'Периодически испытывать тревогу или стресс — это нормально. Помогают техники релаксации, регулярная физическая активность и разговор с тем, кому вы доверяете. Если тревога частая, сильная или мешает повседневной жизни, специалист по психическому здоровью может помочь.',
            'az': 'Vaxtaşırı narahatlıq və ya stress hiss etmək normaldır. Rahatlama üsulları, müntəzəm fiziki fəaliyyət və etibar etdiyiniz biri ilə danışmaq kömək edə bilər. Narahatlıq tez-tez, güclü olursa və ya gündəlik həyata mane olursa, ruhi sağlamlıq mütəxəssisi dəstək ola bilər.',
            'tr': 'Zaman zaman kaygılı veya stresli hissetmek yaygındır. Gevşeme teknikleri, düzenli egzersiz ve güvendiğiniz biriyle konuşmak yardımcı olabilir. Kaygı sık, yoğun ise veya günlük yaşamı etkiliyorsa bir ruh sağlığı uzmanı destek sağlayabilir.',
            'fr': "Se sentir anxieux ou stressé de temps en temps est courant. Des techniques de relaxation, de l'exercice régulier et parler à quelqu'un de confiance peuvent aider. Si l'anxiété est fréquente, intense, ou perturbe votre quotidien, un professionnel de la santé mentale peut vous soutenir.",
            'zh': '偶尔感到焦虑或压力是很常见的。放松技巧、规律运动，以及与信任的人交谈都会有帮助。如果焦虑频繁、强烈，或影响日常生活，心理健康专业人士可以提供支持。',
        },
    },
    {
        'specialization': 'psychiatry',
        'triggers': {
            'en': ['trouble sleeping', 'insomnia', "can't sleep"],
            'ru': ['бессонница', 'проблемы со сном', 'не могу уснуть'],
            'az': ['yuxusuzluq', 'yuxu problemi', 'yata bilmirəm'],
            'tr': ['uyku sorunu', 'uykusuzluk', 'uyuyamıyorum'],
            'fr': ['difficulté à dormir', 'insomnie', 'je ne peux pas dormir'],
            'zh': ['睡眠困难', '失眠', '睡不着'],
        },
        'answers': {
            'en': 'Occasional trouble sleeping is common and can be helped by a consistent bedtime routine, limiting screens before bed, and avoiding caffeine late in the day. See a doctor if sleep problems continue for several weeks or affect your daily functioning.',
            'ru': 'Периодические проблемы со сном — обычное явление, и помогает соблюдение режима отхода ко сну, ограничение экранов перед сном и отказ от кофеина во второй половине дня. Обратитесь к врачу, если проблемы со сном продолжаются несколько недель или мешают повседневной жизни.',
            'az': 'Vaxtaşırı yuxu problemləri normaldır və müntəzəm yatma rejimi, yatmazdan əvvəl ekranlardan uzaqlaşmaq və günün sonunda kofeindən çəkinmək kömək edir. Yuxu problemləri bir neçə həftə davam edirsə və ya gündəlik fəaliyyətə təsir edirsə həkimə müraciət edin.',
            'tr': 'Ara sıra uyku sorunu yaşamak yaygındır ve düzenli bir uyku rutini, yatmadan önce ekranları sınırlamak ve günün geç saatlerinde kafeinden kaçınmak yardımcı olabilir. Uyku sorunları birkaç hafta devam ediyorsa veya günlük yaşamınızı etkiliyorsa bir doktora görünün.',
            'fr': "Avoir occasionnellement du mal à dormir est courant et peut être aidé par une routine de coucher régulière, en limitant les écrans avant de dormir et en évitant la caféine en fin de journée. Consultez un médecin si les problèmes de sommeil persistent plusieurs semaines ou affectent votre quotidien.",
            'zh': '偶尔的睡眠困难很常见，规律的睡前习惯、睡前减少看屏幕、避免下午摄入咖啡因都会有帮助。如果睡眠问题持续数周或影响日常生活，请及时就医。',
        },
    },
    {
        'specialization': 'ophthalmology',
        'triggers': {
            'en': ['red eye', 'eye irritation', 'itchy eyes'],
            'ru': ['красный глаз', 'раздражение глаз', 'зуд в глазах'],
            'az': ['gözün qızarması', 'göz qıcıqlanması', 'gözlərim qaşınır'],
            'tr': ['göz kızarıklığı', 'göz tahrişi', 'gözlerim kaşınıyor'],
            'fr': ['œil rouge', 'irritation des yeux', 'yeux qui démangent'],
            'zh': ['眼睛发红', '眼睛刺激', '眼睛发痒'],
        },
        'answers': {
            'en': "Mild eye redness or irritation is often caused by allergies, dryness, or minor irritation and usually improves on its own. Avoid rubbing your eyes. See an eye doctor if you have pain, vision changes, or symptoms that don't improve within a couple of days.",
            'ru': 'Лёгкое покраснение или раздражение глаз часто вызвано аллергией, сухостью или незначительным раздражением и обычно проходит само. Не трите глаза. Обратитесь к офтальмологу при боли, изменении зрения или если симптомы не проходят за пару дней.',
            'az': 'Yüngül göz qızarması və ya qıcıqlanması çox vaxt allergiya, quruluq və ya kiçik qıcıqlanma ilə bağlıdır və adətən özbaşına keçir. Gözlərinizi ovuşdurmaqdan çəkinin. Ağrı, görmə dəyişikliyi olarsa və ya simptomlar bir-iki gün ərzində keçmirsə göz həkiminə müraciət edin.',
            'tr': 'Hafif göz kızarıklığı veya tahrişi genellikle alerji, kuruluk veya küçük bir tahrişten kaynaklanır ve genellikle kendiliğinden geçer. Gözlerinizi ovuşturmaktan kaçının. Ağrınız, görme değişiklikleriniz varsa veya belirtiler birkaç gün içinde geçmiyorsa bir göz doktoruna görünün.',
            'fr': "Une rougeur ou une irritation légère des yeux est souvent causée par des allergies, la sécheresse ou une irritation mineure, et s'améliore généralement d'elle-même. Évitez de vous frotter les yeux. Consultez un ophtalmologue en cas de douleur, de changements de vision, ou si les symptômes ne s'améliorent pas en quelques jours.",
            'zh': '轻度眼睛发红或刺激通常由过敏、干燥或轻微刺激引起，一般会自行好转。请避免揉眼睛。如果出现疼痛、视力变化，或症状几天内没有改善，请就诊眼科医生。',
        },
    },
    {
        'specialization': 'general_practice',
        'triggers': {
            'en': ['dizziness', 'feeling dizzy', 'lightheaded'],
            'ru': ['головокружение', 'кружится голова', 'сильно закружилась голова'],
            'az': ['başgicəllənmə', 'başım gicəllənir'],
            'tr': ['baş dönmesi', 'başım dönüyor'],
            'fr': ['vertiges', "j'ai des vertiges", 'étourdissement'],
            'zh': ['头晕', '眩晕'],
        },
        'answers': {
            'en': 'Feeling dizzy or lightheaded from time to time is often caused by dehydration, low blood sugar, standing up too quickly, or fatigue. Resting, drinking water, and standing up slowly can help. See a doctor if dizziness is frequent, severe, or comes with fainting, chest pain, or slurred speech.',
            'ru': 'Периодическое головокружение часто вызвано обезвоживанием, низким уровнем сахара в крови, резким вставанием или усталостью. Помогают отдых, вода и медленный подъём на ноги. Обратитесь к врачу, если головокружение частое, сильное или сопровождается обмороком, болью в груди или невнятной речью.',
            'az': 'Vaxtaşırı başgicəllənmə çox vaxt dehidratasiya, qanda aşağı şəkər, tez ayağa qalxma və ya yorğunluqla bağlıdır. İstirahət, su içmək və yavaş qalxmaq kömək edir. Başgicəllənmə tez-tez, güclüdürsə və ya huşunu itirmə, sinədə ağrı, danışıq pozulması ilə müşayiət olunursa həkimə müraciət edin.',
            'tr': 'Ara sıra baş dönmesi genellikle susuzluk, düşük kan şekeri, çok hızlı ayağa kalkma veya yorgunluktan kaynaklanır. Dinlenmek, su içmek ve yavaşça ayağa kalkmak yardımcı olabilir. Baş dönmesi sık, şiddetliyse veya bayılma, göğüs ağrısı ya da konuşma bozukluğu ile birlikteyse bir doktora görünün.',
            'fr': "Se sentir occasionnellement étourdi ou avoir des vertiges est souvent causé par la déshydratation, une glycémie basse, se lever trop vite ou la fatigue. Le repos, l'hydratation et se lever lentement peuvent aider. Consultez un médecin si les vertiges sont fréquents, sévères, ou s'accompagnent d'évanouissement, de douleur thoracique ou de troubles de la parole.",
            'zh': '偶尔头晕通常是由脱水、低血糖、起身过快或疲劳引起的。休息、多喝水、缓慢起身会有帮助。如果头晕频繁、严重，或伴有晕厥、胸痛、口齿不清，请及时就医。',
        },
    },
    {
        'specialization': 'gastroenterology',
        'triggers': {
            'en': ['nausea', 'feeling nauseous', 'vomiting', 'throwing up'],
            'ru': ['тошнота', 'меня тошнит', 'рвота'],
            'az': ['ürəkbulanma', 'ürəyim bulanır', 'qusma', 'qusuram'],
            'tr': ['mide bulantısı', 'midem bulanıyor', 'kusma', 'kusuyorum'],
            'fr': ['nausée', "j'ai des nausées", 'vomissements'],
            'zh': ['恶心', '想吐', '呕吐'],
        },
        'answers': {
            'en': 'Nausea and occasional vomiting are often caused by minor stomach upset, motion sickness, or a mild infection, and usually pass within a day or two. Sipping water and eating bland food can help. See a doctor if vomiting is severe, persistent, contains blood, or comes with a high fever or signs of dehydration.',
            'ru': 'Тошнота и эпизодическая рвота часто вызваны лёгким расстройством желудка, укачиванием или нетяжёлой инфекцией и обычно проходят за день-два. Помогает пить воду небольшими глотками и есть лёгкую пищу. Обратитесь к врачу, если рвота сильная, не проходит, содержит кровь или сопровождается высокой температурой либо признаками обезвоживания.',
            'az': 'Ürəkbulanma və ara-sıra qusma çox vaxt yüngül mədə pozğunluğu, nəqliyyatda xəstələnmə və ya yüngül infeksiya ilə bağlıdır və bir-iki gün ərzində keçir. Az-az su içmək və yüngül qida qəbulu kömək edir. Qusma güclüdürsə, davam edirsə, qanla qarışıqdırsa və ya yüksək hərarət, dehidratasiya əlamətləri ilə müşayiət olunursa həkimə müraciət edin.',
            'tr': 'Bulantı ve ara sıra kusma genellikle hafif mide bozukluğu, taşıt tutması veya hafif bir enfeksiyondan kaynaklanır ve genellikle bir-iki gün içinde geçer. Az az su içmek ve hafif yiyecekler tüketmek yardımcı olabilir. Kusma şiddetliyse, geçmiyorsa, kan içeriyorsa veya yüksek ateş ya da dehidrasyon belirtileriyle birlikteyse bir doktora görünün.',
            'fr': "Les nausées et vomissements occasionnels sont souvent causés par un léger trouble digestif, le mal des transports ou une infection légère, et disparaissent généralement en un jour ou deux. Boire de l'eau à petites gorgées et manger des aliments fades peut aider. Consultez un médecin si les vomissements sont sévères, persistants, contiennent du sang, ou s'accompagnent d'une forte fièvre ou de signes de déshydratation.",
            'zh': '恶心和偶尔呕吐通常由轻度肠胃不适、晕动症或轻微感染引起，一到两天内会好转。少量多次饮水、清淡饮食会有帮助。如果呕吐严重、持续不缓解、带血，或伴有高烧、脱水迹象，请及时就医。',
        },
    },
    {
        'specialization': 'gastroenterology',
        'triggers': {
            'en': ['diarrhea', 'loose stools', 'diarrhoea'],
            'ru': ['диарея', 'понос', 'жидкий стул'],
            'az': ['ishal', 'ishalim var'],
            'tr': ['ishal', 'ishalim var'],
            'fr': ['diarrhée'],
            'zh': ['腹泻', '拉肚子'],
        },
        'answers': {
            'en': 'Diarrhea is usually caused by a mild infection or something you ate and typically clears up within a couple of days. Drink plenty of fluids to avoid dehydration and eat simple, easy-to-digest food. See a doctor if it lasts more than two days, is severe, or comes with blood, high fever, or signs of dehydration.',
            'ru': 'Диарея обычно вызвана лёгкой инфекцией или съеденной пищей и, как правило, проходит за пару дней. Пейте больше жидкости, чтобы избежать обезвоживания, и ешьте простую, легко усваиваемую пищу. Обратитесь к врачу, если диарея длится больше двух дней, сильная или сопровождается кровью, высокой температурой либо признаками обезвоживания.',
            'az': 'İshal adətən yüngül infeksiya və ya yediyiniz qida ilə bağlıdır və bir-iki gün ərzində keçir. Dehidratasiyanın qarşısını almaq üçün çox maye için və sadə, asan həzm olunan qida qəbul edin. İshal iki gündən çox davam edirsə, güclüdürsə və ya qan, yüksək hərarət, dehidratasiya əlamətləri ilə müşayiət olunursa həkimə müraciət edin.',
            'tr': 'İshal genellikle hafif bir enfeksiyon veya yediğiniz bir şeyden kaynaklanır ve genellikle birkaç gün içinde geçer. Dehidrasyonu önlemek için bol sıvı için ve basit, kolay sindirilebilir yiyecekler tüketin. İki günden uzun sürerse, şiddetliyse veya kan, yüksek ateş ya da dehidrasyon belirtileriyle birlikteyse bir doktora görünün.',
            'fr': "La diarrhée est généralement causée par une infection légère ou par ce que vous avez mangé et disparaît habituellement en quelques jours. Buvez beaucoup de liquides pour éviter la déshydratation et mangez des aliments simples et faciles à digérer. Consultez un médecin si elle dure plus de deux jours, est sévère, ou s'accompagne de sang, de forte fièvre ou de signes de déshydratation.",
            'zh': '腹泻通常由轻微感染或饮食引起，一般几天内会好转。请多补充水分以避免脱水，并进食清淡易消化的食物。如果腹泻持续超过两天、严重，或伴有便血、高烧、脱水迹象，请及时就医。',
        },
    },
    {
        'specialization': 'gastroenterology',
        'triggers': {
            'en': ['constipation', 'constipated', "can't poop"],
            'ru': ['запор', 'у меня запор'],
            'az': ['qəbizlik'],
            'tr': ['kabızlık'],
            'fr': ['constipation'],
            'zh': ['便秘'],
        },
        'answers': {
            'en': 'Constipation is common and often related to diet, low fluid intake, or lack of physical activity. Eating more fibre, drinking more water, and staying active can help. See a doctor if it lasts more than a week, is very painful, or comes with blood or unexplained weight loss.',
            'ru': 'Запор — распространённое явление, часто связанное с питанием, недостатком жидкости или низкой физической активностью. Помогает больше клетчатки, воды и физической активности. Обратитесь к врачу, если запор длится больше недели, очень болезненный или сопровождается кровью либо необъяснимой потерей веса.',
            'az': 'Qəbizlik geniş yayılmışdır və çox vaxt qidalanma, az maye qəbulu və ya fiziki fəaliyyətin azlığı ilə bağlıdır. Daha çox lif, su içmək və fiziki aktivlik kömək edir. Bir həftədən çox davam edirsə, çox ağrılıdırsa və ya qan, izahsız çəki itkisi ilə müşayiət olunursa həkimə müraciət edin.',
            'tr': 'Kabızlık yaygındır ve genellikle beslenme, düşük sıvı alımı veya fiziksel hareketsizlikle ilgilidir. Daha fazla lif tüketmek, su içmek ve aktif kalmak yardımcı olabilir. Bir haftadan uzun sürerse, çok ağrılıysa veya kan ya da açıklanamayan kilo kaybıyla birlikteyse bir doktora görünün.',
            'fr': "La constipation est courante et souvent liée à l'alimentation, à un faible apport en liquide ou au manque d'activité physique. Manger plus de fibres, boire plus d'eau et rester actif peut aider. Consultez un médecin si elle dure plus d'une semaine, est très douloureuse, ou s'accompagne de sang ou d'une perte de poids inexpliquée.",
            'zh': '便秘很常见，通常与饮食、饮水不足或缺乏运动有关。多摄入膳食纤维、多喝水、保持运动会有帮助。如果便秘持续超过一周、非常疼痛，或伴有便血、不明原因体重下降，请及时就医。',
        },
    },
    {
        'specialization': 'cardiology',
        'triggers': {
            'en': ['heart palpitations', 'racing heart', 'heart is pounding'],
            'ru': ['учащённое сердцебиение', 'сердце колотится', 'сердцебиение'],
            'az': ['ürək çırpıntısı', 'ürəyim döyünür'],
            'tr': ['çarpıntı', 'kalp çarpıntısı'],
            'fr': ['palpitations', 'mon cœur bat vite'],
            'zh': ['心悸', '心跳加速'],
        },
        'answers': {
            'en': 'Occasional heart palpitations (a racing or pounding heartbeat) can be caused by stress, caffeine, exercise, or lack of sleep, and often pass on their own. Try to relax and avoid stimulants. See a doctor if palpitations are frequent, prolonged, or come with chest pain, dizziness, or shortness of breath.',
            'ru': 'Периодическое учащённое сердцебиение может быть вызвано стрессом, кофеином, физической нагрузкой или недосыпом и часто проходит само. Постарайтесь расслабиться и избегайте стимуляторов. Обратитесь к врачу, если сердцебиение частое, продолжительное или сопровождается болью в груди, головокружением или одышкой.',
            'az': 'Vaxtaşırı ürək çırpıntısı stress, kofein, fiziki fəaliyyət və ya yuxusuzluqla bağlı ola bilər və çox vaxt özbaşına keçir. Rahatlamağa çalışın və stimulyatorlardan çəkinin. Çırpıntı tez-tez, uzunmüddətlidirsə və ya sinədə ağrı, başgicəllənmə, nəfəs darlığı ilə müşayiət olunursa həkimə müraciət edin.',
            'tr': 'Ara sıra kalp çarpıntısı stres, kafein, egzersiz veya uykusuzluktan kaynaklanabilir ve genellikle kendiliğinden geçer. Rahatlamaya çalışın ve uyaranlardan kaçının. Çarpıntı sık, uzun sürüyorsa veya göğüs ağrısı, baş dönmesi ya da nefes darlığı ile birlikteyse bir doktora görünün.',
            'fr': "Des palpitations occasionnelles (battements de cœur rapides ou forts) peuvent être causées par le stress, la caféine, l'exercice ou le manque de sommeil, et disparaissent souvent d'elles-mêmes. Essayez de vous détendre et évitez les stimulants. Consultez un médecin si les palpitations sont fréquentes, prolongées, ou s'accompagnent de douleur thoracique, de vertiges ou d'essoufflement.",
            'zh': '偶尔心悸（心跳加快或强烈跳动）可能由压力、咖啡因、运动或睡眠不足引起，通常会自行缓解。请尝试放松并避免刺激性物质。如果心悸频繁、持续时间长，或伴有胸痛、头晕、气短，请及时就医。',
        },
    },
    {
        'specialization': 'cardiology',
        'triggers': {
            'en': ['high blood pressure', 'blood pressure concern', 'hypertension'],
            'ru': ['высокое давление', 'повышенное давление', 'гипертония'],
            'az': ['yüksək qan təzyiqi', 'hipertoniya'],
            'tr': ['yüksek tansiyon', 'hipertansiyon'],
            'fr': ['hypertension', 'tension artérielle élevée'],
            'zh': ['高血压'],
        },
        'answers': {
            'en': 'Concerns about blood pressure are worth taking seriously, since high blood pressure often has no symptoms but can affect your health over time. Reducing salt intake, staying active, and managing stress can help. Please see a doctor to have your blood pressure checked and discuss next steps.',
            'ru': 'К вопросам о давлении стоит отнестись серьёзно, поскольку повышенное давление часто протекает без симптомов, но со временем может влиять на здоровье. Помогает снижение потребления соли, физическая активность и контроль стресса. Обратитесь к врачу, чтобы проверить давление и обсудить дальнейшие шаги.',
            'az': 'Qan təzyiqi ilə bağlı narahatlıqları ciddi qəbul etmək lazımdır, çünki yüksək qan təzyiqi çox vaxt simptomsuz keçir, lakin zamanla sağlamlığa təsir edə bilər. Duz istehlakının azaldılması, fiziki aktivlik və stresin idarə olunması kömək edir. Qan təzyiqinizi yoxlatmaq və növbəti addımları müzakirə etmək üçün həkimə müraciət edin.',
            'tr': 'Tansiyon konusundaki endişeler ciddiye alınmalıdır, çünkü yüksek tansiyon genellikle belirti vermez ama zamanla sağlığınızı etkileyebilir. Tuz alımını azaltmak, aktif kalmak ve stresi yönetmek yardımcı olabilir. Tansiyonunuzu kontrol ettirmek ve sonraki adımları görüşmek için lütfen bir doktora görünün.',
            'fr': "Les préoccupations concernant la tension artérielle méritent d'être prises au sérieux, car l'hypertension n'a souvent aucun symptôme mais peut affecter votre santé avec le temps. Réduire le sel, rester actif et gérer le stress peut aider. Veuillez consulter un médecin pour faire vérifier votre tension et discuter des prochaines étapes.",
            'zh': '血压问题值得认真对待，因为高血压通常没有症状，但会长期影响健康。减少盐分摄入、保持运动、管理压力会有帮助。请及时就医检查血压并讨论下一步方案。',
        },
    },
    {
        'specialization': 'endocrinology',
        'triggers': {
            'en': ['blood sugar', 'diabetes symptoms', 'excessive thirst'],
            'ru': ['уровень сахара в крови', 'симптомы диабета', 'сильная жажда'],
            'az': ['qan şəkəri', 'diabet əlamətləri', 'güclü susuzluq'],
            'tr': ['kan şekeri', 'diyabet belirtileri', 'aşırı susama'],
            'fr': ['glycémie', 'symptômes de diabète', 'soif excessive'],
            'zh': ['血糖', '糖尿病症状', '口渴严重'],
        },
        'answers': {
            'en': 'Symptoms like excessive thirst, frequent urination, or unexplained tiredness can sometimes be related to blood sugar levels. This is worth discussing with a doctor, who can arrange simple tests to check for diabetes or other causes.',
            'ru': 'Такие симптомы, как сильная жажда, частое мочеиспускание или необъяснимая усталость, иногда могут быть связаны с уровнем сахара в крови. Это стоит обсудить с врачом, который может назначить простые анализы для проверки на диабет или другие причины.',
            'az': 'Güclü susuzluq, tez-tez sidik ifrazı və ya izahsız yorğunluq kimi əlamətlər bəzən qan şəkəri səviyyəsi ilə bağlı ola bilər. Bunu həkimlə müzakirə etmək lazımdır, o, diabet və ya digər səbəbləri yoxlamaq üçün sadə testlər təyin edə bilər.',
            'tr': 'Aşırı susama, sık idrara çıkma veya açıklanamayan yorgunluk gibi belirtiler bazen kan şekeri düzeyleriyle ilgili olabilir. Bunu bir doktorla görüşmekte fayda var; doktor diyabet veya diğer nedenleri kontrol etmek için basit testler isteyebilir.',
            'fr': "Des symptômes comme une soif excessive, des mictions fréquentes ou une fatigue inexpliquée peuvent parfois être liés à la glycémie. Il vaut la peine d'en discuter avec un médecin, qui pourra organiser des tests simples pour vérifier un éventuel diabète ou d'autres causes.",
            'zh': '口渴严重、尿频或不明原因的疲劳等症状有时可能与血糖水平有关。建议与医生讨论，医生可以安排简单的检查来排查糖尿病或其他原因。',
        },
    },
    {
        'specialization': 'ent',
        'triggers': {
            'en': ['ear pain', 'my ear hurts', 'earache'],
            'ru': ['боль в ухе', 'болит ухо'],
            'az': ['qulaq ağrısı', 'qulağım ağrıyır'],
            'tr': ['kulak ağrısı', 'kulağım ağrıyor'],
            'fr': ["douleur à l'oreille", "j'ai mal à l'oreille"],
            'zh': ['耳朵痛', '耳痛'],
        },
        'answers': {
            'en': 'Ear pain is often caused by minor infections, fluid buildup, or irritation, and mild cases often improve within a few days. Avoid inserting anything into the ear. See a doctor if the pain is severe, comes with fever, hearing loss, or discharge.',
            'ru': 'Боль в ухе часто вызвана лёгкой инфекцией, скоплением жидкости или раздражением, и в лёгких случаях обычно проходит за несколько дней. Не вставляйте ничего в ухо. Обратитесь к врачу, если боль сильная, сопровождается температурой, снижением слуха или выделениями.',
            'az': 'Qulaq ağrısı çox vaxt yüngül infeksiya, maye yığılması və ya qıcıqlanma ilə bağlıdır və yüngül hallarda bir neçə gün ərzində yaxşılaşır. Qulağa heç nə salmayın. Ağrı güclüdürsə, hərarət, eşitmə itkisi və ya ifrazatla müşayiət olunursa həkimə müraciət edin.',
            'tr': 'Kulak ağrısı genellikle hafif enfeksiyonlar, sıvı birikimi veya tahrişten kaynaklanır ve hafif vakalar genellikle birkaç gün içinde iyileşir. Kulağa herhangi bir şey sokmaktan kaçının. Ağrı şiddetliyse, ateş, işitme kaybı veya akıntı ile birlikteyse bir doktora görünün.',
            'fr': "La douleur à l'oreille est souvent causée par des infections mineures, une accumulation de liquide ou une irritation, et les cas légers s'améliorent souvent en quelques jours. Évitez d'insérer quoi que ce soit dans l'oreille. Consultez un médecin si la douleur est sévère, s'accompagne de fièvre, de perte auditive ou d'écoulement.",
            'zh': '耳朵痛通常由轻微感染、积液或刺激引起，轻度情况一般几天内会好转。请不要向耳道内塞入任何物品。如果疼痛剧烈，伴有发烧、听力下降或分泌物，请及时就医。',
        },
    },
    {
        'specialization': 'ent',
        'triggers': {
            'en': ['nasal congestion', 'stuffy nose', 'sinus pressure', 'blocked nose'],
            'ru': ['заложенность носа', 'заложен нос', 'давление в пазухах'],
            'az': ['burun tutulması', 'burnum tutulub'],
            'tr': ['burun tıkanıklığı', 'burnum tıkalı', 'sinüs basıncı'],
            'fr': ['nez bouché', 'congestion nasale', 'sinusite'],
            'zh': ['鼻塞', '鼻子不通气'],
        },
        'answers': {
            'en': 'Nasal congestion or sinus pressure is often caused by a cold, allergies, or mild sinus inflammation, and usually improves within a week or two. Steam, fluids, and rest can help. See a doctor if it lasts more than two weeks, is very painful, or comes with a high fever.',
            'ru': 'Заложенность носа или давление в пазухах часто вызваны простудой, аллергией или лёгким воспалением пазух и обычно проходят за одну-две недели. Помогают пар, обильное питьё и отдых. Обратитесь к врачу, если это длится больше двух недель, очень болезненно или сопровождается высокой температурой.',
            'az': 'Burun tutulması və ya sinus təzyiqi çox vaxt soyuqdəymə, allergiya və ya yüngül sinus iltihabı ilə bağlıdır və bir-iki həftə ərzində yaxşılaşır. Buxar, maye qəbulu və istirahət kömək edir. İki həftədən çox davam edirsə, çox ağrılıdırsa və ya yüksək hərarətlə müşayiət olunursa həkimə müraciət edin.',
            'tr': 'Burun tıkanıklığı veya sinüs basıncı genellikle soğuk algınlığı, alerji veya hafif sinüs iltihabından kaynaklanır ve genellikle bir-iki hafta içinde iyileşir. Buhar, sıvı tüketimi ve dinlenme yardımcı olabilir. İki haftadan uzun sürerse, çok ağrılıysa veya yüksek ateşle birlikteyse bir doktora görünün.',
            'fr': "La congestion nasale ou la pression sinusale est souvent causée par un rhume, des allergies ou une inflammation sinusale légère, et s'améliore généralement en une à deux semaines. La vapeur, l'hydratation et le repos peuvent aider. Consultez un médecin si cela dure plus de deux semaines, est très douloureux, ou s'accompagne d'une forte fièvre.",
            'zh': '鼻塞或鼻窦压迫感通常由感冒、过敏或轻度鼻窦炎引起，一般一到两周内会好转。蒸汽、多喝水和休息会有帮助。如果持续超过两周、非常疼痛，或伴有高烧，请及时就医。',
        },
    },
    {
        'specialization': 'urology',
        'triggers': {
            'en': ['painful urination', 'frequent urination', 'burning when i pee'],
            'ru': ['боль при мочеиспускании', 'частое мочеиспускание', 'жжение при мочеиспускании'],
            'az': ['sidik ifrazında ağrı', 'tez-tez sidiyə getmək', 'sidik ifrazında yanma'],
            'tr': ['idrar yaparken ağrı', 'sık idrara çıkma', 'idrar yaparken yanma'],
            'fr': ['douleur en urinant', 'mictions fréquentes', 'brûlure en urinant'],
            'zh': ['排尿疼痛', '尿频', '排尿灼热'],
        },
        'answers': {
            'en': 'Discomfort or a burning feeling when urinating can be a sign of a urinary tract infection, which is common and treatable. Drinking plenty of water can help in the meantime. Please see a doctor for proper testing and treatment, especially if symptoms persist or you notice blood or fever.',
            'ru': 'Дискомфорт или жжение при мочеиспускании могут быть признаком инфекции мочевыводящих путей — это распространённое и излечимое состояние. Пока помогает обильное питьё. Обратитесь к врачу для правильной диагностики и лечения, особенно если симптомы сохраняются или вы заметили кровь либо температуру.',
            'az': 'Sidik ifrazı zamanı narahatlıq və ya yanma hissi sidik yollarının infeksiyasının əlaməti ola bilər — bu geniş yayılmış və müalicə oluna bilən vəziyyətdir. Bu müddətdə çox su içmək kömək edir. Düzgün müayinə və müalicə üçün, xüsusən simptomlar davam edərsə və ya qan ya hərarət müşahidə edərsinizsə, həkimə müraciət edin.',
            'tr': 'İdrar yaparken rahatsızlık veya yanma hissi, yaygın ve tedavi edilebilir bir durum olan idrar yolu enfeksiyonunun bir belirtisi olabilir. Bu arada bol su içmek yardımcı olabilir. Doğru test ve tedavi için, özellikle belirtiler devam ederse veya kan ya da ateş fark ederseniz bir doktora görünün.',
            'fr': "Une gêne ou une sensation de brûlure en urinant peut être un signe d'infection urinaire, une affection courante et traitable. Boire beaucoup d'eau peut aider en attendant. Veuillez consulter un médecin pour un dépistage et un traitement appropriés, surtout si les symptômes persistent ou si vous remarquez du sang ou de la fièvre.",
            'zh': '排尿时不适或有灼热感可能是尿路感染的迹象，这是常见且可治疗的情况。同时多喝水会有帮助。请及时就医进行检查和治疗，尤其是症状持续存在或出现血尿、发烧时。',
        },
    },
    {
        'specialization': 'gynecology',
        'triggers': {
            'en': ['period pain', 'menstrual cramps', 'irregular periods'],
            'ru': ['боль при месячных', 'менструальные боли', 'нерегулярные месячные'],
            'az': ['aybaşı ağrısı', 'menstrual ağrılar', 'nizamsız aybaşı'],
            'tr': ['adet ağrısı', 'regl sancısı', 'düzensiz adet'],
            'fr': ['douleurs menstruelles', 'règles douloureuses', 'règles irrégulières'],
            'zh': ['痛经', '月经不规律'],
        },
        'answers': {
            'en': 'Mild menstrual cramps are common and can often be eased with rest, heat, and over-the-counter pain relief. See a gynecologist if the pain is severe, periods are very irregular, or symptoms interfere with daily life.',
            'ru': 'Лёгкие менструальные боли — обычное явление, и часто помогают отдых, тепло и безрецептурные обезболивающие. Обратитесь к гинекологу, если боль сильная, месячные очень нерегулярны или симптомы мешают повседневной жизни.',
            'az': 'Yüngül aybaşı ağrıları geniş yayılmışdır və çox vaxt istirahət, isti kompres və reseptsiz ağrıkəsicilərlə yüngülləşir. Ağrı güclüdürsə, aybaşı çox nizamsızdırsa və ya simptomlar gündəlik həyata mane olursa ginekoloqa müraciət edin.',
            'tr': 'Hafif adet sancıları yaygındır ve genellikle dinlenme, sıcak uygulama ve reçetesiz ağrı kesicilerle hafifletilebilir. Ağrı şiddetliyse, adet düzeniniz çok düzensizse veya belirtiler günlük yaşamınızı etkiliyorsa bir jinekoloğa görünün.',
            'fr': "Les crampes menstruelles légères sont courantes et peuvent souvent être soulagées par le repos, la chaleur et des antalgiques en vente libre. Consultez un gynécologue si la douleur est sévère, si les règles sont très irrégulières, ou si les symptômes perturbent votre quotidien.",
            'zh': '轻度痛经很常见，通常通过休息、热敷和非处方止痛药可以缓解。如果疼痛剧烈、月经非常不规律，或症状影响日常生活，请就诊妇科医生。',
        },
    },
    {
        'specialization': 'neurology',
        'triggers': {
            'en': ['numbness', 'tingling', 'pins and needles'],
            'ru': ['онемение', 'покалывание', 'мурашки'],
            'az': ['keyimə', 'sızıltı'],
            'tr': ['uyuşma', 'karıncalanma'],
            'fr': ['engourdissement', 'fourmillements'],
            'zh': ['麻木', '刺痛感'],
        },
        'answers': {
            'en': 'Mild numbness or tingling, especially after sitting or lying in one position, is usually harmless and passes quickly once you move. See a doctor if it is frequent, does not go away, affects one side of your body suddenly, or comes with weakness or difficulty speaking (these need urgent evaluation).',
            'ru': 'Лёгкое онемение или покалывание, особенно после долгого сидения или лежания в одной позе, обычно безобидно и быстро проходит при движении. Обратитесь к врачу, если это частое, не проходит, внезапно затронуло одну сторону тела или сопровождается слабостью либо затруднённой речью (это требует срочной оценки).',
            'az': 'Xüsusilə uzun müddət bir vəziyyətdə oturduqdan və ya uzandıqdan sonra yaranan yüngül keyimə və ya sızıltı adətən zərərsizdir və hərəkət etdikdə tez keçir. Bu tez-tez olursa, keçmirsə, qəfil bədənin bir tərəfinə təsir edirsə və ya zəiflik, danışıq çətinliyi ilə müşayiət olunursa (bu təcili qiymətləndirmə tələb edir) həkimə müraciət edin.',
            'tr': 'Özellikle bir süre aynı pozisyonda oturduktan veya yattıktan sonra ortaya çıkan hafif uyuşma veya karıncalanma genellikle zararsızdır ve hareket ettiğinizde hızla geçer. Bu sık oluyorsa, geçmiyorsa, aniden vücudunuzun bir tarafını etkiliyorsa veya güçsüzlük ya da konuşma güçlüğü ile birlikteyse (bunlar acil değerlendirme gerektirir) bir doktora görünün.',
            'fr': "Un léger engourdissement ou des fourmillements, surtout après être resté assis ou allongé dans une même position, sont généralement sans gravité et disparaissent rapidement en bougeant. Consultez un médecin si cela est fréquent, ne disparaît pas, touche soudainement un côté du corps, ou s'accompagne de faiblesse ou de difficultés à parler (cela nécessite une évaluation urgente).",
            'zh': '轻度麻木或刺痛感，尤其是久坐或久躺同一姿势后出现的，通常无害，活动后很快会消失。如果这种情况频繁发生、持续不消退、突然影响身体一侧，或伴有无力、说话困难（这些需要紧急评估），请及时就医。',
        },
    },
    {
        'specialization': 'general_practice',
        'triggers': {
            'en': ['allergies', 'sneezing', 'seasonal allergies', 'hay fever'],
            'ru': ['аллергия', 'чихание', 'сезонная аллергия'],
            'az': ['allergiya', 'asqırma', 'mövsümi allergiya'],
            'tr': ['alerji', 'hapşırma', 'mevsimsel alerji'],
            'fr': ['allergies', 'éternuements', 'rhume des foins'],
            'zh': ['过敏', '打喷嚏', '季节性过敏'],
        },
        'answers': {
            'en': "Sneezing, a runny nose, and itchy eyes are common signs of seasonal allergies. Avoiding known triggers and over-the-counter antihistamines can often help. See a doctor if symptoms are severe, don't improve, or start interfering with sleep or daily activities.",
            'ru': 'Чихание, насморк и зуд в глазах — распространённые признаки сезонной аллергии. Часто помогает избегание известных триггеров и безрецептурные антигистаминные препараты. Обратитесь к врачу, если симптомы тяжёлые, не проходят или начинают мешать сну или повседневной активности.',
            'az': 'Asqırma, burun axması və gözlərin qaşınması mövsümi allergiyanın geniş yayılmış əlamətləridir. Məlum triggerlərdən çəkinmək və reseptsiz antihistamin dərmanlar çox vaxt kömək edir. Simptomlar güclüdürsə, yaxşılaşmırsa və ya yuxuya, gündəlik fəaliyyətə mane olmağa başlayırsa həkimə müraciət edin.',
            'tr': 'Hapşırma, burun akıntısı ve gözlerde kaşıntı mevsimsel alerjinin yaygın belirtileridir. Bilinen tetikleyicilerden kaçınmak ve reçetesiz antihistaminikler genellikle yardımcı olur. Belirtiler şiddetliyse, iyileşmiyorsa veya uykuyu ya da günlük aktiviteleri etkilemeye başlıyorsa bir doktora görünün.',
            'fr': "Les éternuements, le nez qui coule et les yeux qui démangent sont des signes courants d'allergies saisonnières. Éviter les déclencheurs connus et des antihistaminiques en vente libre peuvent souvent aider. Consultez un médecin si les symptômes sont sévères, ne s'améliorent pas, ou commencent à perturber le sommeil ou les activités quotidiennes.",
            'zh': '打喷嚏、流鼻涕和眼睛发痒是季节性过敏的常见表现。避免已知诱因和服用非处方抗组胺药通常会有帮助。如果症状严重、没有改善，或开始影响睡眠或日常活动，请及时就医。',
        },
    },
    {
        'specialization': 'dermatology',
        'triggers': {
            'en': ['acne', 'pimples', 'breakouts'],
            'ru': ['акне', 'прыщи', 'высыпания на лице'],
            'az': ['sızanaq', 'sızanaqlar'],
            'tr': ['sivilce', 'akne'],
            'fr': ['acné', 'boutons'],
            'zh': ['痤疮', '粉刺', '长痘'],
        },
        'answers': {
            'en': "Mild acne is very common and can often be managed with gentle skincare, keeping skin clean, and avoiding picking at spots. Over-the-counter treatments can also help. See a dermatologist if acne is severe, painful, leaves scars, or doesn't improve with basic care.",
            'ru': 'Лёгкое акне очень распространено и часто поддаётся контролю с помощью бережного ухода за кожей, поддержания её чистоты и отказа от выдавливания высыпаний. Также могут помочь безрецептурные средства. Обратитесь к дерматологу, если акне сильное, болезненное, оставляет рубцы или не проходит при базовом уходе.',
            'az': 'Yüngül sızanaq çox geniş yayılmışdır və çox vaxt nəzakətli dəri qulluğu, dərinin təmiz saxlanması və sıxılmaması ilə idarə oluna bilər. Reseptsiz vasitələr də kömək edə bilər. Sızanaq güclüdürsə, ağrılıdırsa, iz buraxırsa və ya sadə qulluqla yaxşılaşmırsa dermatoloqa müraciət edin.',
            'tr': 'Hafif akne çok yaygındır ve genellikle nazik cilt bakımı, cildi temiz tutmak ve sivilceleri sıkmaktan kaçınmakla kontrol altına alınabilir. Reçetesiz tedaviler de yardımcı olabilir. Akne şiddetliyse, ağrılıysa, iz bırakıyorsa veya temel bakımla iyileşmiyorsa bir dermatoloğa görünün.',
            'fr': "L'acné légère est très courante et peut souvent être gérée avec des soins de peau doux, en gardant la peau propre et en évitant de percer les boutons. Des traitements en vente libre peuvent aussi aider. Consultez un dermatologue si l'acné est sévère, douloureuse, laisse des cicatrices, ou ne s'améliore pas avec des soins de base.",
            'zh': '轻度痤疮非常常见，通常通过温和护肤、保持皮肤清洁、避免抠挤痘痘可以控制。非处方药膏也会有帮助。如果痤疮严重、疼痛、留疤，或基础护理后没有改善，请就诊皮肤科医生。',
        },
    },
    {
        'specialization': 'orthopedics',
        'triggers': {
            'en': ['muscle cramps', 'leg cramps', 'muscle spasm'],
            'ru': ['судороги мышц', 'судороги в ногах', 'мышечный спазм'],
            'az': ['əzələ tutması', 'ayaq tutması', 'əzələ spazmı'],
            'tr': ['kas krampı', 'bacak krampı', 'kas spazmı'],
            'fr': ['crampes musculaires', 'crampes aux jambes', 'spasme musculaire'],
            'zh': ['肌肉痉挛', '腿抽筋'],
        },
        'answers': {
            'en': 'Muscle cramps, especially in the legs, are often caused by dehydration, overuse, or low levels of minerals like potassium or magnesium. Gentle stretching, hydration, and a balanced diet can help. See a doctor if cramps are frequent, severe, or do not improve.',
            'ru': 'Мышечные судороги, особенно в ногах, часто вызваны обезвоживанием, перенапряжением или низким уровнем минералов, таких как калий или магний. Помогают лёгкая растяжка, достаточное количество жидкости и сбалансированное питание. Обратитесь к врачу, если судороги частые, сильные или не проходят.',
            'az': 'Xüsusilə ayaqlarda əzələ tutması çox vaxt dehidratasiya, həddindən artıq yüklənmə və ya kalium, maqnezium kimi minerallarin aşağı səviyyəsi ilə bağlıdır. Yüngül dartınma, kifayət qədər maye qəbulu və balanslaşdırılmış qidalanma kömək edir. Tutmalar tez-tez, güclüdürsə və ya keçmirsə həkimə müraciət edin.',
            'tr': 'Özellikle bacaklardaki kas krampları genellikle susuzluk, aşırı kullanım veya potasyum ya da magnezyum gibi minerallerin düşük seviyelerinden kaynaklanır. Hafif esneme, sıvı alımı ve dengeli beslenme yardımcı olabilir. Kramplar sık, şiddetliyse veya geçmiyorsa bir doktora görünün.',
            'fr': "Les crampes musculaires, surtout dans les jambes, sont souvent causées par la déshydratation, une sursollicitation ou de faibles niveaux de minéraux comme le potassium ou le magnésium. Des étirements doux, l'hydratation et une alimentation équilibrée peuvent aider. Consultez un médecin si les crampes sont fréquentes, sévères, ou ne s'améliorent pas.",
            'zh': '肌肉痉挛，尤其是腿部抽筋，通常由脱水、过度使用或钾、镁等矿物质水平偏低引起。适度拉伸、补水和均衡饮食会有帮助。如果抽筋频繁、严重，或没有改善，请及时就医。',
        },
    },
]


class Command(BaseCommand):
    help = 'Seeds/updates the starter ResponseTemplate bank (draft content — see module docstring).'

    def handle(self, *args, **options):
        created_count = 0
        updated_count = 0
        for entry in TEMPLATES:
            en_answer = entry['answers']['en']
            template, created = ResponseTemplate.objects.update_or_create(
                answers__en=en_answer,
                defaults={
                    'specialization': entry['specialization'],
                    'triggers': entry['triggers'],
                    'answers': entry['answers'],
                    'is_active': True,
                },
            )
            if created:
                created_count += 1
            else:
                updated_count += 1
        self.stdout.write(self.style.SUCCESS(
            f'Seeded {len(TEMPLATES)} templates ({created_count} created, {updated_count} updated).'
        ))
        self.stdout.write(self.style.WARNING(
            'Draft content — have a doctor and a native speaker per language review '
            'wording before relying on this in production.'
        ))
