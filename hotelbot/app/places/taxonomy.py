"""Travel taxonomy: 19 top-level categories + an open subcategory vocabulary.

The database stores `category` + free-text `subcategory` + attributes, so a
new kind of place ("eSIM kiosk", "padel court") needs no schema change: add
it to a data pack, and optionally register labels/keywords here so guests
can ask for it by name.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field

from app.text import latin_to_cyrillic


class Category(str, enum.Enum):
    ACCOMMODATION = "ACCOMMODATION"
    FOOD = "FOOD"
    NIGHTLIFE = "NIGHTLIFE"
    TRANSPORT = "TRANSPORT"
    RENTAL = "RENTAL"
    GUIDE = "GUIDE"
    TOUR = "TOUR"
    ACTIVITY = "ACTIVITY"
    ATTRACTION = "ATTRACTION"
    CULTURE = "CULTURE"
    EVENT = "EVENT"
    SHOPPING = "SHOPPING"
    WELLNESS = "WELLNESS"
    ESSENTIAL_SERVICE = "ESSENTIAL_SERVICE"
    HEALTH = "HEALTH"
    CONNECTIVITY = "CONNECTIVITY"
    FINANCIAL_SERVICE = "FINANCIAL_SERVICE"
    MOBILITY_INFRASTRUCTURE = "MOBILITY_INFRASTRUCTURE"
    OTHER = "OTHER"


@dataclass(frozen=True)
class Subcategory:
    key: str
    category: Category
    labels: dict[str, str]
    # Phrases (en/cnr/ru, matched on folded text) that ask for this kind of place.
    keywords: tuple[str, ...] = field(default_factory=tuple)


def _s(key: str, cat: Category, en: str, cnr: str, ru: str, *kw: str) -> Subcategory:
    return Subcategory(key, cat, {"en": en, "cnr": cnr, "ru": ru}, tuple(kw))


C = Category
SUBCATEGORIES: dict[str, Subcategory] = {s.key: s for s in [
    # FOOD
    _s("restaurant", C.FOOD, "restaurant", "restoran", "ресторан", "restaurant*", "restoran*", "ресторан*"),
    _s("konoba", C.FOOD, "traditional tavern", "konoba", "конoба", "konoba", "tavern", "traditional food", "local food",
       "montenegrin food", "domaca hrana", "tradicionaln*", "местн* кухн*", "национальн* кухн*"),
    _s("cafe", C.FOOD, "cafe", "kafić", "кафе", "cafe", "coffee", "kafic*", "kafa", "kafu", "kofe", "кофе", "кафе"),
    _s("bakery", C.FOOD, "bakery", "pekara", "пекарня", "bakery", "bread", "pastry", "pekar*", "burek", "пекарн*", "выпечк*"),
    _s("fast_food", C.FOOD, "fast food", "brza hrana", "фастфуд", "fast food", "burger*", "pizza", "brza hrana",
       "pica", "фастфуд", "бургер*", "пицц*"),
    _s("dessert", C.FOOD, "desserts", "poslastičarnica", "десерты", "dessert*", "ice cream", "cake", "poslasti*",
       "sladoled", "торт*", "мороженое", "десерт*"),
    _s("food_market", C.SHOPPING, "food market", "pijaca", "рынок", "market", "pijac*", "рынок", "рынке"),
    # NIGHTLIFE
    _s("bar", C.NIGHTLIFE, "bar", "bar", "бар", "bar", "bars", "бар", "бары", "бара"),
    _s("cocktail_bar", C.NIGHTLIFE, "cocktail bar", "koktel bar", "коктейль-бар", "cocktail*", "koktel*", "коктейл*"),
    _s("wine_bar", C.NIGHTLIFE, "wine bar", "vinski bar", "винный бар", "wine", "vino", "vinsk*", "вино", "винн*"),
    _s("pub", C.NIGHTLIFE, "pub", "pab", "паб", "pub", "pab", "паб"),
    _s("nightclub", C.NIGHTLIFE, "nightclub", "noćni klub", "ночной клуб", "nightclub", "club", "clubbing", "dancing",
       "nocni klub", "disko*", "клуб", "ночн* клуб*"),
    _s("live_music_venue", C.NIGHTLIFE, "live music venue", "svirka uživo", "живая музыка", "concert bar",
       "live music venue"),
    # CULTURE / ATTRACTION
    _s("museum", C.CULTURE, "museum", "muzej", "музей", "museum*", "muzej*", "музе*"),
    _s("gallery", C.CULTURE, "gallery", "galerija", "галерея", "galler*", "galerij*", "галере*"),
    _s("theater", C.CULTURE, "theatre", "pozorište", "театр", "theat*", "pozorist*", "театр*"),
    _s("cinema", C.CULTURE, "cinema", "bioskop", "кинотеатр", "cinema", "movie*", "film", "bioskop*", "кино"),
    _s("religious_site", C.CULTURE, "religious site", "vjerski objekat", "храм", "church", "monastery", "mosque",
       "crkv*", "manastir*", "церк*", "монастыр*"),
    _s("historic_site", C.ATTRACTION, "historic site", "istorijski lokalitet", "историческое место", "histor*",
       "architect*", "monument*", "ruins", "istorij*", "spomenik*", "архитектур*", "истори*", "памятник*"),
    _s("viewpoint", C.ATTRACTION, "viewpoint", "vidikovac", "смотровая площадка", "viewpoint", "view", "panorama",
       "vidikovac*", "смотров*", "вид на"),
    _s("park", C.ATTRACTION, "park", "park", "парк", "park", "парк*"),
    _s("national_park", C.ATTRACTION, "national park", "nacionalni park", "национальный парк", "national park",
       "nacionalni park", "национальн* парк*"),
    _s("lake", C.ATTRACTION, "lake", "jezero", "озеро", "lake", "jezer*", "озер*"),
    _s("beach", C.ACTIVITY, "beach", "plaža", "пляж", "beach", "plaz*", "пляж*"),
    _s("ski_resort", C.ACTIVITY, "ski resort", "skijalište", "горнолыжный курорт", "ski resort", "slope*", "skijalist*",
       "горнолыжн*", "склон*"),
    # WELLNESS
    _s("spa", C.WELLNESS, "spa", "spa", "спа", "spa", "massage", "sauna", "wellness", "masaz*", "sauna", "массаж*",
       "сауна", "спа"),
    _s("gym", C.WELLNESS, "gym", "teretana", "спортзал", "gym", "fitness", "teretan*", "фитнес", "спортзал*"),
    # SHOPPING
    _s("supermarket", C.SHOPPING, "supermarket", "supermarket", "супермаркет", "supermarket", "grocer*", "groceries",
       "market", "prodavnic*", "market", "супермаркет*", "продукт*", "магазин"),
    _s("shopping_center", C.SHOPPING, "shopping centre", "tržni centar", "торговый центр", "shopping", "mall",
       "trzni centar", "торгов* центр*"),
    _s("electronics_store", C.SHOPPING, "electronics shop", "prodavnica elektronike", "магазин электроники",
       "charger", "electronics", "adapter", "phone shop", "punjac*", "elektronik*", "зарядк*", "электроник*", "адаптер*"),
    _s("outdoor_store", C.SHOPPING, "outdoor equipment shop", "planinarska oprema", "туристическое снаряжение",
       "hiking boots", "outdoor gear", "outdoor equipment", "planinarsk* oprem*", "снаряжени*", "ботинки"),
    _s("souvenir_shop", C.SHOPPING, "souvenir shop", "suveniri", "сувениры", "souvenir*", "suvenir*", "сувенир*"),
    # ESSENTIAL / HEALTH / FINANCE / CONNECTIVITY
    _s("pharmacy", C.HEALTH, "pharmacy", "apoteka", "аптека", "pharmacy", "chemist", "medicine", "apotek*", "lijek*",
       "аптек*", "лекарств*"),
    _s("hospital", C.HEALTH, "hospital", "bolnica", "больница", "hospital", "bolnic*", "больниц*"),
    _s("clinic", C.HEALTH, "clinic / health centre", "dom zdravlja", "поликлиника", "clinic", "doctor's office",
       "dom zdravlja", "ambulant*", "поликлиник*", "клиник*"),
    _s("dentist", C.HEALTH, "dentist", "stomatolog", "стоматолог", "dentist", "tooth*", "zub*", "stomatolog*",
       "стоматолог*", "зуб*"),
    _s("atm", C.FINANCIAL_SERVICE, "ATM", "bankomat", "банкомат", "atm", "cash machine", "cash", "bankomat*",
       "keš", "kes", "банкомат*", "наличн*"),
    _s("bank", C.FINANCIAL_SERVICE, "bank", "banka", "банк", "bank", "bank*", "банк"),
    _s("currency_exchange", C.FINANCIAL_SERVICE, "currency exchange", "mjenjačnica", "обмен валюты", "exchange money",
       "currency exchange", "mjenjacnic*", "обмен* валют*", "обменник*"),
    _s("sim_shop", C.CONNECTIVITY, "SIM / eSIM", "SIM kartica", "SIM-карта", "sim", "esim", "e-sim", "mobile data",
       "sim kartic*", "сим", "симк*", "есим", "мобильн* интернет*"),
    _s("coworking", C.CONNECTIVITY, "coworking", "kovorking", "коворкинг", "coworking", "co-working",
       "work for", "place to work", "laptop", "kovorking*", "raditi", "коворкинг*", "поработать", "ноутбук*"),
    _s("print_shop", C.ESSENTIAL_SERVICE, "printing", "štampanje", "печать", "print*", "stampa*", "kopirnic*",
       "распечат*", "печать"),
    _s("post_office", C.ESSENTIAL_SERVICE, "post office", "pošta", "почта", "post office", "parcel", "posta",
       "paket*", "почт*", "посылк*"),
    _s("laundry", C.ESSENTIAL_SERVICE, "laundry", "perionica", "прачечная", "laundry", "washing", "perionic*",
       "prac*", "прачечн*", "постират*"),
    _s("luggage_storage", C.ESSENTIAL_SERVICE, "luggage storage", "garderoba", "камера хранения", "luggage", "bags",
       "left luggage", "garderob*", "prtljag", "багаж*", "камер* хранени*"),
    _s("public_toilet", C.ESSENTIAL_SERVICE, "public toilet", "javni toalet", "общественный туалет", "toilet", "wc",
       "toalet", "туалет*"),
    _s("tourist_info", C.ESSENTIAL_SERVICE, "tourist information", "turistički info centar", "туристический центр",
       "tourist info*", "turisticki info*", "туристическ* информ*"),
    _s("police", C.ESSENTIAL_SERVICE, "police", "policija", "полиция", "police", "polic*", "полици*"),
    # MOBILITY INFRASTRUCTURE
    _s("bus_station", C.MOBILITY_INFRASTRUCTURE, "bus station", "autobuska stanica", "автовокзал", "bus", "coach",
       "autobus*", "автобус*", "автовокзал*"),
    _s("car_park", C.MOBILITY_INFRASTRUCTURE, "car park", "parking", "парковка", "car park", "parking", "park the car",
       "parking", "parkiral*", "парковк*", "припарков*"),
    _s("fuel_station", C.MOBILITY_INFRASTRUCTURE, "fuel station", "benzinska pumpa", "заправка", "fuel", "petrol",
       "gas station", "benzin*", "pumpa", "заправк*", "бензин*"),
    _s("ev_charger", C.MOBILITY_INFRASTRUCTURE, "EV charger", "punjač za električna vozila", "зарядка для электромобилей",
       "ev charg*", "electric car", "charging station", "elektricn* auto*", "punjac za auto*", "электромобил*"),
    # RENTAL / GUIDE / TOUR / ACTIVITY (usually sold as Offerings)
    _s("ski_rental", C.RENTAL, "ski rental", "rent-a-ski", "прокат лыж", "ski rental", "rent skis", "skis",
       "najam skija", "skije", "лыж*", "прокат лыж*"),
    _s("bike_rental", C.RENTAL, "bike rental", "rent-a-bike", "прокат велосипедов", "bike*", "bicycle*", "bicikl*",
       "велосипед*"),
    _s("guide", C.GUIDE, "guide", "vodič", "гид", "guide", "vodic*", "гид*", "экскурсовод*"),
    _s("rafting", C.ACTIVITY, "rafting", "rafting", "рафтинг", "rafting", "рафтинг*"),
    _s("hiking", C.ACTIVITY, "hiking", "planinarenje", "поход", "hike", "hiking", "trek*", "planinar*", "поход*"),
]}


def label(subcategory: str, locale: str) -> str:
    sub = SUBCATEGORIES.get(subcategory)
    if sub is None:
        return subcategory.replace("_", " ")
    if locale == "cnr-Cyrl":
        return latin_to_cyrillic(sub.labels["cnr"])
    return sub.labels.get(locale.split("-")[0]) or sub.labels["en"]


def category_of(subcategory: str, fallback: str = "OTHER") -> str:
    sub = SUBCATEGORIES.get(subcategory)
    return sub.category.value if sub else fallback


# Generic words name a family, not one kind: "a bar" includes cocktail and wine bars.
FAMILIES = {
    "restaurant": {"restaurant", "konoba", "fast_food"},
    "bar": {"bar", "cocktail_bar", "wine_bar", "pub", "live_music_venue"},
}
