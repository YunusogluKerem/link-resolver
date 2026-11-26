# Link Çözümleme Servisi

Platformlarda paylaşılan linkleri (çoğu kısa link) son adresine kadar takip eder, yönlendirme zincirini saklar ve linkte bir ürün markası geçip geçmediğini işaretler.

Stack: Python 3.12, Django 5.2 (LTS), Django REST Framework, simplejwt, httpx (async, HTTP/2), openpyxl, SQLite, Docker.

## Hızlı başlangıç (Docker)

```bash
# 1. Ayarlar: örnek dosyayı kopyalayın, anahtarı ve admin şifresini değiştirin
cp .env.example .env

# 2. Excel dosyasını data/ klasörüne koyun (repoda yoktur, bkz. "Veri")
cp /path/to/dosya.xlsx data/links.xlsx

# 3. Servisi başlatın
docker compose up -d

# 4. Linkleri içeri alın ve çözün
docker compose exec web python manage.py import_links data/links.xlsx
docker compose exec web python manage.py resolve_links
```

API `http://localhost:8000/api/`, admin paneli `http://localhost:8000/admin/` adresindedir. Kullanım örnekleri için "API" bölümüne bakın.

Konteyner her açılışta şunları yapar (`docker-entrypoint.sh`):

1. `migrate`: eksik migration varsa uygular.
2. `ensure_admin`: `.env` içindeki `DJANGO_SUPERUSER_USERNAME` ve `DJANGO_SUPERUSER_PASSWORD` ile admin kullanıcıyı **yoksa** oluşturur. Kullanıcı varsa dokunmaz; panelden değiştirilen şifre yeniden başlatmada sıfırlanmaz.
3. `gunicorn` ile uygulamayı başlatır.

Docker ile ilgili kararlar:

- **SQLite dosyası bir volume'dadır** (`sqlite_data` → `/app/db`). `docker compose down`, yeniden başlatma ve imajın yeniden derlenmesi veriyi silmez. Her şeyi sıfırlamak için: `docker compose down -v`.
- **Excel imaja girmez.** `data/` klasörü konteynere salt okunur bağlanır; `.dockerignore` müşteri verisini, `.env` dosyasını ve yerel veritabanını imajın dışında tutar.
- **Gizli değerler yalnızca `.env` dosyasındadır**; bu dosya git'e girmez. Repoda yalnızca yer tutucu değerler içeren `.env.example` vardır.
- **`gunicorn` ve `whitenoise`:** Django'nun `runserver` komutu yalnızca geliştirme içindir, bu yüzden uygulamayı `gunicorn` çalıştırır. `whitenoise` admin panelinin CSS/JS dosyalarını sunar; böylece ayrı bir nginx konteyneri gerekmez.
- Uygulama konteynerde root olmayan bir kullanıcıyla çalışır.

## Kurulum (yerel, Docker'sız)

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python manage.py migrate

.venv/bin/python manage.py import_links data/links.xlsx
.venv/bin/python manage.py resolve_links
.venv/bin/python manage.py createsuperuser
DJANGO_DEBUG=true .venv/bin/python manage.py runserver

# Testler (ağa çıkmaz)
.venv/bin/python manage.py test links
```

## Komutlar

| Komut | Ne yapar |
|---|---|
| `import_links <dosya.xlsx>` | Excel'deki linkleri `Links` tablosuna alır. Tekrar çalıştırılabilir; kayıt çoğaltmaz. |
| `resolve_links` | Linkleri çözer, sonuçları `LinkDetails` tablosuna yazar. |
| `detect_brands` | Marka alanlarını ağa çıkmadan yeniden hesaplar (bkz. "Marka tespiti"). |
| `ensure_admin` | Ortam değişkenlerinden admin kullanıcıyı yoksa oluşturur. |

`resolve_links` tek komutla yeterlidir: tüm linkleri çözdükten sonra geçici görünen hataları (`timeout`, `error` ve "çok fazla istek" anlamına gelen 429) kendiliğinden bir kez daha dener. Bu hataların çoğu (yük altında yavaşlayan site, kopan bağlantı) ikinci denemede düzelir.

`resolve_links` seçenekleri:

| Seçenek | Varsayılan | Açıklama |
|---|---|---|
| `--concurrency` | 50 | Aynı anda işlenen link sayısı |
| `--per-host` | 8 | Tek bir domaine aynı anda atılan istek sayısı |
| `--timeout` | 10 | İstek başına saniye |
| `--limit N` | yok | Deneme için en fazla N benzersiz URL |
| `--retry-failed` | kapalı | Önceki çalıştırmalarda `timeout`, `error` veya 429 ile bitenleri de işle (elle kullanım için) |
| `--force` | kapalı | Hepsini baştan çöz |

Komut tekrar çalıştırılabilir: varsayılan olarak yalnızca henüz çözülmemiş linkleri işler.

## Veri

Kaynak Excel müşteri verisidir ve repoya girmez (`.gitignore`: `*.xlsx`, `data/*`). Dosyayı `data/` klasörüne kendiniz koyun; repoda klasörün yalnızca boş hali vardır.

Dosya bir veritabanı dökümüdür (`select id, links from app1_story`). `links` kolonu Postgres dizisi olduğu için her hücre `{https://...}` biçimindedir; virgül veya boşluk içeren URL'lerde `{"https://..."}`. Import bu sarmalı ayıklar; bir hücrede birden fazla link olursa her biri ayrı satır olur.

Örnek dosya: 4000 satır, 4000 benzersiz `id`, 3305 benzersiz link. Aynı link 695 satırda farklı bir içerikle tekrar eder. Bu satırlar atılmaz, çünkü her içeriğin kendi kaydı olmalıdır; çözümleme ise her benzersiz URL'ye tek istek atar ve sonucu o linki taşıyan tüm satırlara yazar.

## Veri modeli

Görev dokümanındaki şema aynen korunmuştur.

**Links**: `id`, `content_id` (Excel'deki `id`), `link` (ayıklanmış ham link). `(content_id, link)` çifti tekildir; import'un ikinci kez çalışması kayıt çoğaltmaz.

**LinkDetails**: `id`, `link_id`, `link_detail` (son URL), `brand_exist`.

`link_id` bire-bir ilişkidir (`OneToOneField`): kolon adı şemadaki gibidir, her link için tek detay satırı garanti edilir ve çözümleme tekrar çalışınca satır güncellenir.

Şemaya eklenen alanlar:

| Alan | Gerekçe |
|---|---|
| `brand_name` | `brand_exist` yalnızca var/yok der; raporlama için hangi marka olduğu da gerekir. |
| `marketplace_brand_id` | Trendyol zincirindeki `BrandIds` değeri (ör. `670` veya `617,101640`). İleride bir ID→isim tablosuyla marka adına çevrilebilsin diye saklanır. |
| `redirect_chain` | Görevin asıl amacı yönlendirmeleri toplamak; her adımın URL'si ve HTTP kodu JSON olarak saklanır. Son adımda, sayfada varsa `og:site_name` değeri de (`site_name`) durur. |
| `status` | "Çözüldü", "site engelledi" ve "hata" durumlarını ayırmak için. |
| `status_code` | Son adımın HTTP kodu; hata ayıklamayı kolaylaştırır. |
| `resolved_at` | Linkler zamanla değişir; sonucun ne zamana ait olduğu bilinmelidir. |

### `status` değerleri

| Değer | Anlamı | `link_detail` |
|---|---|---|
| `ok` | Son sayfa normal cevap verdi. | Son URL |
| `resolved` | Yönlendirmeler izlendi ve son URL biliniyor, ama o sayfa bizi reddetti (401/403/429). | Son URL |
| `blocked` | Daha ilk istek reddedildi; yönlendirme olup olmadığı bilinmiyor. | Boş |
| `timeout` | İstek zaman aşımına uğradı. | Zincirde ilerlendiyse ulaşılan son URL, değilse boş |
| `error` | DNS hatası, 404/5xx, bozuk URL, çok fazla yönlendirme. | Aynı kural |

Kural: `link_detail` yalnızca ilk isteğin kendisi başarısız olduğunda boştur.

## Çözümleme nasıl çalışır

- **Async:** tek bir `httpx.AsyncClient` (bağlantı havuzu, HTTP/2), `asyncio.Semaphore` ile sınırlı eşzamanlılık.
- **Yönlendirmeler elle izlenir** (`follow_redirects=True` yerine her adım ayrı istek). Böylece zincir hata anında da kaybolmaz, limit domain başına uygulanır (`ty.gl` → `adj.st` → `trendyol.com` üç ayrı domain) ve 429/503 her adımda yeniden denenebilir.
- **Domain başına limit ve retry:** bir domaine aynı anda en fazla 8 istek; 429/503'te `Retry-After` (en fazla 10 sn) veya üstel bekleme ile en fazla 2 yeniden deneme.
- **GET, ama sayfa indirilmez:** cevap stream olarak açılır, başlıklar okunur, bağlantı kapatılır. Yalnızca 200 dönen HTML sayfaların ilk 64 KB'ına `meta refresh` ve basit JS yönlendirmesi için bakılır.
- **İstemci tarafı yönlendirme (best effort):** 5 saniyeden kısa `meta refresh` her sayfada, JS yönlendirmesi yalnızca 10 KB'tan küçük ara sayfalarda izlenir. `<noscript>` içindekiler ve sayfanın kendisine dönen yönlendirmeler (çerez/bot kontrolü) izlenmez.
- **Tarayıcı başlıkları:** gerçekçi `User-Agent`, `Accept` ve `Accept-Language` gönderilir. Hepsiburada'nın kısa link domaini (`app.hb.biz`) `Accept-Language` olmayan isteklere 403 döner.
- **Hata toleransı:** hiçbir link komutu durdurmaz; her hata bir `status` olur ve nedeni zincirin son adımına yazılır.
- **Yazma:** sonuçlar 500'lük gruplar halinde `bulk_create` (upsert) ile yazılır; yarıda kesilen çalıştırma ilerlemesini kaybetmez.

## API

Tüm uçlar yalnızca admin (`is_staff`) kullanıcıya açıktır (`IsAdminUser`). Kimlik doğrulama JWT iledir.

**1. Token al**

```bash
curl -X POST http://localhost:8000/api/token/ \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "password": "<şifre>"}'
# {"refresh": "...", "access": "eyJhbGciOi..."}
```

**2. Link detaylarını sayfa sayfa çek** (sayfa başına 1000 kayıt)

```bash
curl --compressed "http://localhost:8000/api/link-details/?page=1" \
  -H "Authorization: Bearer <access token>"
```

```json
{
  "count": 4000,
  "total_pages": 4,
  "current_page": 1,
  "remaining_pages": 3,
  "next": "http://localhost:8000/api/link-details/?page=2",
  "previous": null,
  "results": [
    {
      "id": 1,
      "link_id": 1,
      "content_id": 123456789,
      "link": "https://ty.gl/abc123?fbclid=...",
      "link_detail": "https://www.trendyol.com/magaza/x-m-123456?...",
      "brand_exist": false,
      "brand_name": null,
      "marketplace_brand_id": "",
      "status": "resolved",
      "status_code": 403,
      "redirect_chain": [
        {"url": "https://ty.gl/abc123?fbclid=...", "status": 302},
        {"url": "https://p8zh.adj.st/token?...", "status": 302},
        {"url": "https://www.trendyol.com/magaza/x-m-123456?...", "status": 403}
      ],
      "resolved_at": "2026-11-25T17:53:35.396281Z"
    }
  ]
}
```

- `total_pages` toplam sayfa, `remaining_pages` bu sayfadan sonra kalan sayfa sayısıdır; son sayfada `0` ve `next` `null` olur.
- Liste `select_related` ile tek sorguda çekilir; satır başına ek sorgu (N+1) yoktur. Sayfa başına toplam 3 sorgu çalışır: kullanıcı, sayım, liste.
- 1000 kayıtlık bir sayfa yaklaşık 2,2 MB JSON'dur; `GZipMiddleware` ile ağda yaklaşık 220 KB'a iner (`curl --compressed`).
- Django admin paneli `/admin/` adresindedir; iki model de liste, filtre ve arama ile görünür.

### Güvenlik notu: token ömrü

Görev gereği access token ömrü yaklaşık 4 yıldır (1460 gün). Bu bilinçli bir ödünleşimdir: sızan bir token süresi dolana kadar geçerlidir ve tek tek iptal edilemez; tek çare `DJANGO_SECRET_KEY`'i değiştirmektir, bu da tüm token'ları geçersiz kılar.

Production'da kısa ömürlü access token ile refresh token tercih edilir. Bunun için kod değişikliği gerekmez: `JWT_ACCESS_TOKEN_DAYS=0.01` (yaklaşık 15 dakika) yapılır ve access token `POST /api/token/refresh/` ile yenilenir.

### Ortam değişkenleri

| Değişken | Varsayılan | Açıklama |
|---|---|---|
| `DJANGO_SECRET_KEY` | geliştirme anahtarı | JWT imzası da bu anahtarla atılır; production'da mutlaka değiştirin |
| `DJANGO_DEBUG` | `false` | |
| `DJANGO_ALLOWED_HOSTS` | `localhost,127.0.0.1` | Virgülle ayrılmış liste |
| `SQLITE_PATH` | `db.sqlite3` | Veritabanı dosyasının yolu |
| `JWT_ACCESS_TOKEN_DAYS` | `1460` | Access token ömrü (gün, ondalık olabilir) |
| `JWT_REFRESH_TOKEN_DAYS` | `1460` | Refresh token ömrü |
| `DJANGO_SUPERUSER_USERNAME` | yok | Açılışta oluşturulacak admin kullanıcı (yoksa oluşturulur) |
| `DJANGO_SUPERUSER_PASSWORD` | yok | Bu kullanıcının şifresi; boşsa kullanıcı oluşturulmaz |
| `DJANGO_SUPERUSER_EMAIL` | boş | |
| `WEB_PORT` | `8000` | Docker'da API'nin açılacağı port |

## Marka tespiti

Amaç pazaryerini değil, linkte geçen ürün markasını bulmaktır. Yöntem bilerek basit ve açıklanabilir tutulmuştur.

**Marka listesi koddan ayrı bir dosyadadır: `links/brands.txt`.** Her satır bir markadır (`Görünen Ad | takma ad, takma ad`); listeye marka eklemek kod değişikliği gerektirmez.

**Liste veya yöntem değişince linkleri yeniden çözmek gerekmez:**

```bash
python manage.py detect_brands
```

Bu komut ağa hiç çıkmaz; kayıtlı son URL'ler ve zincirler üzerinden `brand_exist`, `brand_name` ve `marketplace_brand_id` alanlarını saniyeler içinde yeniden hesaplar.

Kurallar (`links/brands.py`):

1. **Pazaryeri ürün sayfaları, URL yapısından:**
   - Trendyol `/{marka}/{ürün}-p-{id}`: ilk segment markadır.
   - Hepsiburada `/{slug}-p-{sku}`: slug'ın ilk 1-3 kelimesi marka listesindeyse markadır, değilse marka yok sayılır.
2. **Pazaryeri mağaza, kampanya ve arama sayfaları:** adın başı marka listesindeyse sayılır (`/magaza/estee-lauder`, `/ara?q=mavi+boxer`). Mağaza adı tek başına marka değildir; `/magaza/og-store` marka sayılmaz.
3. **Diğer tüm URL'ler:** listedeki bir marka path'te, arama veya kampanya parametresinde ya da sitenin kendi alan adında tam kelime olarak geçiyorsa sayılır (`mac`, `macbook` ile eşleşmez).
4. **Markanın kendi sitesi:** yukarıdakiler sonuç vermediyse ve site "marka olmayan alan adları" listesinde değilse, site markanın kendi sitesi kabul edilir ve sitenin adı marka sayılır (`www.akcabutik.com` → `Akcabutik`, `de.checkout.gymshark.com` → `Gymshark`).

**Marka olmayan alan adları da ayrı bir dosyadadır: `links/non_brand_domains.txt`.** Bölümleri: pazaryeri, çok markalı perakendeci (Boyner, CarrefourSA, iHerb), sosyal medya, haber, link kısaltıcı, link-in-bio, platform (bilet, form, müzik, site kurucu), kurum (`.org`, `.gov.tr`, `.edu.tr`), bahis, elle kontrol edilip marka olmadığı görülen siteler ve "belirsiz". "Belirsiz" bölümünde örnek veride görülen, adından ve URL'sinden ne olduğu anlaşılmayan siteler durur; biri kontrol edene kadar marka sayılmazlar. Perakendecide satılan bir marka URL'de geçiyorsa yine sayılır (`boyner.com.tr/kiko-milano-...` → `KIKO Milano`). Bir satır bir alan adı (`youtube.com`, alt alan adlarıyla), bir uzantı (`org`) veya her uzantıdaki bir ad (`amazon.*`) olabilir. Dosyaya satır eklemek kod değişikliği gerektirmez.

4. kuralda marka adı iki kaynaktan gelir:

- **`og:site_name`:** çözümleme sırasında son sayfanın zaten okunan ilk 64 KB'ında bu etiket varsa değeri zincirin son adımına `site_name` olarak yazılır (ek istek yoktur). 40 karakterden uzun (slogan) veya alan adının kendisi olan değerler kullanılmaz.
- **Alan adı:** `site_name` yoksa ad alan adından türetilir; alt alan adı ve uzantı (`com.tr`, `co.uk`) atılır. Ad marka listesinde varsa listedeki yazımı kullanılır (`www2.hm.com` → `H&M`).

Aynı zamanda gündelik kelime olan markalar (Mavi, Eti, Yumoş) listede `^` ile işaretlidir ve yalnızca bir pazaryeri adının başında sayılır; `bebe-mavi-kazak` içindeki "mavi" marka değildir.

Çözülemeyen linklerde (`link_detail` boş) marka ham linkte aranır.

Tek giriş noktası `detect_brand(url, site_name)` fonksiyonudur. İleride AI tabanlı bir yöntem aynı imzayla bu fonksiyonun yerine konabilir; çözümleyiciye ve API'ye dokunmak gerekmez, `detect_brands` komutu mevcut kayıtları yeniden işaretler.

## Örnek veride sonuçlar

Aşağıdaki rakamlar 25 Kasım 2026'da temiz bir Docker ortamında (`docker compose up`, boş volume) alındı: 3305 benzersiz URL 208 saniyede çözüldü (yaklaşık 16 URL/sn, yeniden denemeler dahil).

| Status | Satır | Oran |
|---|---|---|
| `ok` | 1502 | %37,6 |
| `resolved` | 2401 | %60,0 |
| `blocked` | 79 | %2,0 |
| `error` | 18 | %0,5 |
| `timeout` | 0 | %0 |

Tablo, bu çalıştırmanın ve birkaç dakika sonra yapılan bir `resolve_links --retry-failed` çalıştırmasının ardından oluşan durumdur. İlk çalıştırma bittiğinde `blocked` 100 satırdı; 429 alan linklerin bir kısmı sonradan çözüldü.

4000 satırın 3907'sinde son URL bilinir (`link_detail` dolu); 93 satırda ilk istek başarısız olduğu için boştur.

`brand_exist`: 4000 satırın 690'ı (%17,3).

| Son URL | Satır | Marka bulunan | Oran |
|---|---|---|---|
| Trendyol | 2269 | 30 | %1,3 |
| Hepsiburada | 712 | 285 | %40,0 |
| Diğer siteler | 1019 | 375 | %36,8 |
| **Toplam** | 4000 | 690 | %17,3 |

"Markanın kendi sitesi" kuralının (4. kural) etkisi, aynı kayıtlar üzerinde `detect_brands` ile ağa çıkmadan ölçüldü:

| Son URL | Kuraldan önce | Kuraldan sonra |
|---|---|---|
| Trendyol | 30 (%1,3) | 30 (%1,3) |
| Hepsiburada | 285 (%40,0) | 285 (%40,0) |
| Diğer siteler | 28 (%2,7) | 375 (%36,8) |
| **Toplam** | 343 (%8,6) | 690 (%17,3) |

Daha önce marka bulunan satırlardan yalnızca 4'ü markasını kaybetti, o da bilerek: Sephora bir mağaza zinciri olduğu için marka listesinden çıkarılıp perakendecilere taşındı (Gratis, Watsons ve Rossmann ile birlikte). Başka hiçbir satırın markası değişmedi. "Diğer siteler"deki 1019 satırın dağılımı:

| Kategori | Satır | Marka bulunan |
|---|---|---|
| Markanın kendi sitesi (listede olmayan 147 alan adı) | 366 | 366 |
| Sosyal medya | 199 | 0 |
| Haber | 123 | 3 |
| Diğer pazaryerleri (Amazon, Shopier, uygulama mağazaları) | 99 | 0 |
| Platform (bilet, form, müzik) | 81 | 0 |
| Çok markalı perakendeci | 57 | 6 |
| Belirsiz | 30 | 0 |
| Link-in-bio | 29 | 0 |
| Link kısaltıcı (yönlendirmesi izlenemeyenler) | 14 | 0 |
| Elle kontrol edildi, marka değil | 12 | 0 |
| Kurum | 8 | 0 |
| Bahis | 1 | 0 |

**Elle kontrol:** 60 alan adlık örnekte (ilk 40 + rastgele 20) doğruluk %77–90 (6 yanlış, 8 belirsiz). Bu ölçüm düzeltmelerden önce yapıldı. Örnek, marka sayılan 153 alan adından satır sayısına göre ilk 40 ile kalanlardan rastgele seçilen 20 alan adıdır; kontrol sitelere girilmeden, yalnızca ada bakılarak yapıldı. Alt sınır belirsizleri yanlış, üst sınır doğru sayar. Bulunan 6 yanlış sonradan listeye işlendi (perakendeci veya "marka değil"); 8 belirsiz olduğu gibi bırakıldı.

**Rakamlar çalıştırmadan çalıştırmaya değişir.** Aynı gün içinde, aynı IP'den yapılan önceki bir çalıştırmada sonuç `ok` 1679, `resolved` 2256, `blocked` 46 idi. Geliştirme sırasında aynı siteler birkaç kez tarandığı için son çalıştırmada bazıları daha sıkı davrandı: Hepsiburada 136 satırda son sayfada 403 döndü (önceki çalıştırmada hepsi 200 idi), bazı küçük mağaza siteleri de ürün sayfalarında 429 verdi. Son URL çoğunda yine de bilindiği için marka oranı değişmedi (342 → 343).

## Bilinen sınırlamalar

- **Trendyol'da marka adı çoğunlukla URL'de yok, yalnızca ID var.** Trendyol kısa linklerinin yaklaşık %96'sı ürün sayfasına değil, satıcı mağazasına (`/magaza/x-m-123456`) veya ID'lerle tanımlı bir aramaya (`/sr?mid=...`) gider. URL'de ve zincirdeki `adj.st` parametrelerinde sayısal ID'ler bulunur. ID'den isme gitmek için Trendyol sayfasını okumak gerekir; o sayfa da 403 döner. Bir eşleme tablosuyla neyin kazanılacağı ID'nin türüne bağlıdır:
  - **Marka ID'si (`BrandIds`)** 2266 Trendyol satırının yalnızca 75'inde doludur (%3,3; 30 farklı değer). Bu değerler `marketplace_brand_id` alanında saklanır. Marka ID→isim tablosu Trendyol'daki oranı %1,3'ten en fazla %4,6'ya, genel oranı %17,3'ten %19,1'e çıkarır.
  - **Satıcı ID'si (`MerchantId`)** satırların yaklaşık %96'sında vardır ve `redirect_chain` içinde durur. Asıl büyük artış satıcı ID→satıcı adı tablosundan gelir; ancak satıcı her zaman marka değildir (resmi marka mağazaları marka sayılır, çok markalı satıcılar sayılmaz).
- **Marka oranları iyimserdir.** Marka listesi (`links/brands.txt`) bu örnek veride görülen markalarla genişletildi. Daha önce görülmemiş linklerde isabet oranı daha düşük olacaktır; liste elle bakım ister.
- **"Markanın kendi sitesi" bir varsayımdır.** Listede olmayan her site marka sayılır; yani kural hariç tutulanlar listesi kadar doğrudur. Liste örnek verideki alan adlarından ve bilinen sitelerden derlendi; siteler tek tek ziyaret edilmedi, adlarına bakılarak sınıflandırıldı. Yeni veride listede olmayan bir haber sitesi veya platform yanlışlıkla marka sayılır. Listede olmayan kişisel siteler (bir doktorun, bir eğitmenin, bir sanatçının sitesi) de marka sayılır. Çok markalı perakendeciler ve mağaza zincirleri (Sephora, Gratis, Watsons) sayılmaz. Elle kontrol edilen 60 alan adlık örnekte doğruluk %77–90 çıktı (bkz. "Örnek veride sonuçlar"); ölçüm yalnızca ada bakılarak yapıldı ve kontrol edilmeyen 93 alan adını kapsamaz. Bu yüzden %17,3 bir üst sınır gibi okunmalıdır.
- **Alan adından türetilen ad kabadır.** Kelimeler ayrılamaz ve Türkçe karakterler geri gelmez: `sedayalcinatelier.com` → `Sedayalcinatelier`. `og:site_name` bunu düzeltir, ama örnek verideki sitelerde nadiren vardır: denenen 14 gerçek sayfanın 3'ünde bulundu, yalnızca 1'i kullanılabilirdi (biri slogan, biri alan adının kendisiydi). Sayfa HTML'i saklanmadığı için mevcut kayıtlarda `site_name` yoktur; bu kayıtlarda ad alan adından gelir. `site_name` bir sonraki `resolve_links` çalıştırmasında dolar.
- **Bot koruması.** Trendyol son sayfada 403 döner (`resolved`: son URL yine de bilinir). 79 satır ilk istekte reddedildi (`blocked`): 46'sı 401/403 (Passo, Sephora, lnk.bio gibi siteler), 33'ü 429. Koruma aşılmaya çalışılmaz.
- **Hız sınırı (429).** Bazı siteler aynı IP'den kısa sürede gelen istekleri sınırlar ve 60 saniye beklenmesini ister. Komut bu kadar beklemez (bekleme en fazla 10 sn); sonda bir kez daha dener, yine 429 alırsa satır `blocked` kalır. Bu satırlar bir süre sonra `resolve_links --retry-failed` ile yeniden denenebilir.
- **JS ile yapılan yönlendirmeler.** Tarayıcı çalıştırılmaz. Yalnızca `meta refresh` ve küçük sayfalardaki düz `location = "..."` kalıpları yakalanır; karmaşık JS yönlendirmeleri kaçar.
- **Geçici hatalar.** İlk geçişte 43 URL zaman aşımı veya hata ile bitti; komutun sonundaki otomatik ikinci denemede 25'i çözüldü. Kalan 18 `error` kalıcıdır (ölü linkler, WhatsApp'ın 400 döndüğü linkler, bozuk URL'ler).
- **HTTPS yok.** Servis düz HTTP ile çalışır; bu yerel kullanım içindir. Production'da önüne TLS sonlandıran bir proxy konur ve Django'nun güvenli çerez/HSTS ayarları açılır.
- **Sonuç ana bağlıdır.** Kısa linkler zamanla başka yere yönlenebilir veya silinebilir; `resolved_at` bu yüzden tutulur.

## Ölçeklenme notu

Bu çözüm 4000 link için tek süreçte, yaklaşık 3-4 dakikada işini bitirir. Hacim büyürse (yüz binlerce link, sürekli akış) sırasıyla şunlar değişir:

- **Veritabanı: Postgres.** SQLite aynı anda tek yazara izin verir. Birden fazla işçi süreç aynı anda sonuç yazacaksa Postgres gerekir. Modeller ve sorgular standart Django ORM olduğu için geçiş yalnızca `DATABASES` ayarıdır; upsert (`bulk_create(update_conflicts=True)`) Postgres'te de aynı çalışır.
- **Kuyruk ve işçiler: Celery (veya benzeri bir kuyruk).** `resolve_links` bugün elle çalışan tek bir komuttur. Büyük hacimde linkler kuyruğa yazılır, birden fazla işçi kuyruktan okur; yeni link geldikçe otomatik çözülür, çöken işçinin işi başkasına geçer. `LinkResolver` sınıfı Django komutundan bağımsızdır, bir Celery görevi içinden aynen çağrılabilir.
- **Batch işleme.** Bugün "çözülecek linkler" tek seferde belleğe alınır; 4000 satırda sorun değildir. Milyonlarca satırda linkler sayfa sayfa (ör. 10.000'lik gruplar) okunmalı ve her grup ayrı bir iş olmalıdır. Sonuçların 500'lük gruplarla yazılması zaten bu yapıya uygundur.
- **Domain başına hız sınırı ortak tutulmalı.** Bugünkü limit tek sürecin içindedir. Birden fazla işçi olunca aynı siteye toplamda kaç istek atıldığını ortak bir yerde (ör. Redis) saymak gerekir; yoksa işçi sayısı arttıkça 429 ve engellemeler artar.
- **Çıkış IP'si.** Yukarıdaki sonuçların gösterdiği gibi, aynı IP'den tekrar tekrar tarama engellenmeyi artırır. Büyük hacimde istekler zamana yayılmalı, tekrar çözümleme aralığı (`resolved_at` üzerinden) planlanmalıdır.
- **API.** 1000'lik sayfa `count` sorgusu ve `OFFSET` kullanır; milyonlarca kayıtta derin sayfalar yavaşlar. O noktada `id` üzerinden ilerleyen (keyset/cursor) sayfalama tercih edilir.

Bunların hiçbiri bu görevin ölçeğinde gerekli değildir; bu yüzden projede Celery, Redis veya Postgres yoktur.
