"""AI symptom assistant pipeline — fully local, no external AI API calls.

Chat flow per user message (``handle_user_message``):

1. Emergency keyword check — deterministic, checked against all supported
   languages (not just the patient's) since a patient may type in a
   different language than their profile setting. Highest priority; skips
   template matching entirely.
2. Template match — fuzzy match against the active ``ResponseTemplate``
   bank, patient's language only.
3. Off-topic keyword match — canned ``not_medical_refusal`` for clearly
   non-medical topics (weather, sports, finance, ...).
4. Otherwise — canned ``assistant_unclear`` ("I don't understand").
"""
from django.db.models import Avg, Count, F
from rapidfuzz import fuzz

from apps.core.i18n import city_label, resolve_city_key
from apps.notifications.i18n import recipient_language
from apps.users.i18n import specialization_label

from .i18n import assistant_message
from .models import Message, ResponseTemplate

_MATCH_THRESHOLD = 75  # rapidfuzz token_set_ratio, 0-100

# Draft lists — reviewed for engineering integration only. MUST be reviewed
# by a native speaker / clinical translator per language before production,
# especially _EMERGENCY_KEYWORDS (this is the only safety net for red-flag
# symptoms now that no AI reads every message).
_EMERGENCY_KEYWORDS = {
    'en': ['chest pain', "can't breathe", 'difficulty breathing', 'shortness of breath',
           'stroke', 'face drooping', 'slurred speech', 'sudden weakness on one side',
           'severe bleeding', "bleeding won't stop", 'unconscious', 'passed out',
           'severe allergic reaction', 'anaphylaxis', 'throat closing', 'swelling of face or throat'],
    'ru': ['боль в груди', 'давит в груди', 'не могу дышать', 'трудно дышать', 'нехватка воздуха',
           'инсульт', 'перекосило лицо', 'невнятная речь', 'онемела одна сторона',
           'сильное кровотечение', 'кровь не останавливается', 'потеря сознания', 'потерял сознание',
           'тяжёлая аллергическая реакция', 'анафилаксия', 'отёк горла', 'отёк лица'],
    'az': ['sinədə ağrı', 'nəfəs ala bilmirəm', 'nəfəs darlığı', 'insult', 'üzün əyilməsi',
           'danışıq pozulması', 'bir tərəfdə zəiflik', 'güclü qanaxma', 'qanaxma dayanmır',
           'huşunu itirmək', 'ağır allergik reaksiya', 'boğazın şişməsi'],
    'tr': ['göğüs ağrısı', 'nefes alamıyorum', 'nefes darlığı', 'inme', 'felç belirtileri',
           'yüzde çarpıklık', 'konuşma bozukluğu', 'vücudun bir tarafında güçsüzlük',
           'şiddetli kanama', 'kanama durmuyor', 'bilinç kaybı', 'bayılma',
           'şiddetli alerjik reaksiyon', 'boğazda şişme'],
    'fr': ['douleur thoracique', 'je ne peux pas respirer', 'difficulté à respirer', 'essoufflement',
           'accident vasculaire cérébral', 'avc', 'visage affaissé', 'difficulté à parler',
           "faiblesse soudaine d'un côté", 'saignement important', "saignement ne s'arrête pas",
           'perte de conscience', 'évanouissement', 'réaction allergique sévère', 'gorge qui se ferme'],
    'zh': ['胸痛', '无法呼吸', '呼吸困难', '气短', '中风', '面部下垂', '口齿不清',
           '一侧突然无力', '大出血', '出血不止', '失去意识', '昏迷', '严重过敏反应', '喉咙肿胀'],
}

# Lighter-stakes draft list — only decides which of two fallback messages to
# show, so tone/coverage can be improved over time without a safety review.
_OFF_TOPIC_KEYWORDS = {
    'en': ['weather', 'football', 'soccer', 'basketball', 'exchange rate', 'stock market',
           'currency', 'election', 'politics', 'movie', 'music', 'celebrity', 'lottery'],
    'ru': ['погода', 'футбол', 'баскетбол', 'курс валют', 'курс доллара', 'биржа',
           'выборы', 'политика', 'фильм', 'музыка', 'знаменитост', 'лотерея'],
    'az': ['hava', 'futbol', 'basketbol', 'valyuta məzənnəsi', 'birja', 'seçki',
           'siyasət', 'film', 'musiqi', 'lotereya'],
    'tr': ['hava durumu', 'futbol', 'basketbol', 'döviz kuru', 'borsa', 'seçim',
           'siyaset', 'film', 'müzik', 'piyango'],
    'fr': ['météo', 'football', 'basket', 'taux de change', 'bourse', 'élection',
           'politique', 'film', 'musique', 'loterie'],
    'zh': ['天气', '足球', '篮球', '汇率', '股市', '选举', '政治', '电影', '音乐', '彩票'],
}


def _is_emergency(text):
    """Checked against ALL languages, not just the patient's — a patient may
    type in a different language than their profile setting, and here it
    matters more to not miss a red flag than to avoid an occasional
    over-match."""
    lowered = text.lower()
    return any(
        kw.lower() in lowered
        for keywords in _EMERGENCY_KEYWORDS.values()
        for kw in keywords
    )


def _off_topic_keyword_match(text):
    lowered = text.lower()
    return any(
        kw.lower() in lowered
        for keywords in _OFF_TOPIC_KEYWORDS.values()
        for kw in keywords
    )


def _match_template(text, lang):
    """Returns the active ResponseTemplate with the highest fuzzy-match
    score against ``text``, restricted to templates that define triggers for
    ``lang``. None if nothing scores at or above _MATCH_THRESHOLD."""
    best_template, best_score = None, 0
    for template in ResponseTemplate.objects.filter(is_active=True):
        triggers = template.triggers.get(lang, [])
        if not triggers:
            continue
        score = max(fuzz.token_set_ratio(text, trigger) for trigger in triggers)
        if score > best_score:
            best_template, best_score = template, score
    return best_template if best_score >= _MATCH_THRESHOLD else None


def search_doctors(specialization, city=None, lang='en'):
    """Top-5 verified doctors for a specialization (optionally in a city),
    ordered by average review rating. Deliberately a narrow standalone query —
    do not reuse/refactor DoctorListView for this."""
    from apps.subscriptions.entitlements import entitled_doctor_filter
    from apps.users.models import User

    qs = (
        User.objects
        .filter(
            role=User.ROLE_DOCTOR,
            doctor_profile__is_verified=True,
            doctor_profile__specialization=specialization,
        )
        .filter(**entitled_doctor_filter())
        .select_related('doctor_profile')
        .prefetch_related('workplaces')
        .annotate(
            avg_rating=Avg('doctor_reviews__rating'),
            total_reviews=Count('doctor_reviews', distinct=True),
        )
    )
    if city:
        city_key = resolve_city_key(city)
        qs = qs.filter(workplaces__city=city_key).distinct() if city_key else qs.none()
    qs = qs.order_by(F('avg_rating').desc(nulls_last=True), 'first_name', 'last_name')[:5]

    results = []
    for doctor in qs:
        workplaces = list(doctor.workplaces.all())
        primary = next((w for w in workplaces if w.is_primary), None)
        if primary is None and workplaces:
            primary = workplaces[0]
        results.append({
            'id': str(doctor.id),
            'first_name': doctor.first_name,
            'last_name': doctor.last_name,
            'specialization': specialization,
            'specialization_display': specialization_label(specialization, lang),
            'average_rating': (
                round(doctor.avg_rating, 1) if doctor.avg_rating is not None else None
            ),
            'total_reviews': doctor.total_reviews,
            'city': primary.city if primary else '',
            'city_display': city_label(primary.city, lang) if primary else '',
        })
    return results


def _touch_conversation(conversation):
    # auto_now only fires on Conversation.save(); message saves don't cascade.
    conversation.save(update_fields=['updated_at'])


def handle_user_message(conversation, patient, text):
    """Runs the local template-matching pipeline for one user message and
    returns the persisted assistant ``Message``. See module docstring for
    the 4-step order."""
    lang = recipient_language(patient)

    Message.objects.create(
        conversation=conversation, role=Message.ROLE_USER, content=text
    )

    if _is_emergency(text):
        reply = Message.objects.create(
            conversation=conversation,
            role=Message.ROLE_ASSISTANT,
            content=assistant_message('emergency_warning', lang),
        )
        _touch_conversation(conversation)
        return reply

    template = _match_template(text, lang)
    if template is not None:
        suggested_doctors = []
        if template.specialization:
            suggested_doctors = search_doctors(template.specialization, lang=lang)
        disclaimer = assistant_message('disclaimer', lang)
        content = f"{template.answers.get(lang, '')}\n\n{disclaimer}"
        reply = Message.objects.create(
            conversation=conversation,
            role=Message.ROLE_ASSISTANT,
            content=content,
            suggested_doctors=suggested_doctors,
        )
        _touch_conversation(conversation)
        return reply

    message_key = 'not_medical_refusal' if _off_topic_keyword_match(text) else 'assistant_unclear'
    reply = Message.objects.create(
        conversation=conversation,
        role=Message.ROLE_ASSISTANT,
        content=assistant_message(message_key, lang),
    )
    _touch_conversation(conversation)
    return reply
