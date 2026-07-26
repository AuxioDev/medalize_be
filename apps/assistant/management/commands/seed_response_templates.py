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
