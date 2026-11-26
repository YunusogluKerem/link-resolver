# Tasarım notları

Bu dosya [README.md](README.md) dosyasının devamıdır: verinin ve modelin yapısı, çözümlemenin işleyişi, marka tespiti kuralları ve alınan kararların gerekçeleri. Sonuçlar ve sınırlamaların ayrıntısı [RESULTS.md](RESULTS.md) dosyasındadır.

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

### `resolve_links` seçenekleri

`resolve_links` tek komutla yeterlidir: tüm linkleri çözdükten sonra geçici görünen hataları (`timeout`, `error` ve "çok fazla istek" anlamına gelen 429) kendiliğinden bir kez daha dener. Bu hataların çoğu (yük altında yavaşlayan site, kopan bağlantı) ikinci denemede düzelir.

| Seçenek | Varsayılan | Açıklama |
|---|---|---|
| `--concurrency` | 50 | Aynı anda işlenen link sayısı |
| `--per-host` | 8 | Tek bir domaine aynı anda atılan istek sayısı |
| `--timeout` | 10 | İstek başına saniye |
| `--limit N` | yok | Deneme için en fazla N benzersiz URL |
| `--retry-failed` | kapalı | Önceki çalıştırmalarda `timeout`, `error` veya 429 ile bitenleri de işle (elle kullanım için) |
| `--force` | kapalı | Hepsini baştan çöz |

Komut tekrar çalıştırılabilir: varsayılan olarak yalnızca henüz çözülmemiş linkleri işler.

## Docker kararları

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

## Yerel kurulum (Docker'sız)

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
4. **Markanın kendi sitesi:** yukarıdakiler sonuç vermediyse ve site "marka olmayan alan adları" listesinde değilse, site markanın kendi sitesi kabul edilir ve sitenin adı marka sayılır (`www.akcabutik.com` → `Akcabutik`, `de.checkout.gymshark.com` → `Gymshark`). Ad, sayfada varsa `og:site_name` etiketinden, yoksa alan adından gelir.

**Marka olmayan alan adları da ayrı bir dosyadadır: `links/non_brand_domains.txt`** (pazaryeri, çok markalı perakendeci, sosyal medya, haber, link kısaltıcı ve benzeri bölümler). Dosyaya satır eklemek kod değişikliği gerektirmez. Bölümlerin, satır biçiminin ve marka adının nereden geldiğinin ayrıntısı: [RESULTS.md](RESULTS.md#marka-tespiti-ayrıntıları).

Aynı zamanda gündelik kelime olan markalar (Mavi, Eti, Yumoş) listede `^` ile işaretlidir ve yalnızca bir pazaryeri adının başında sayılır; `bebe-mavi-kazak` içindeki "mavi" marka değildir.

Çözülemeyen linklerde (`link_detail` boş) marka ham linkte aranır.

Tek giriş noktası `detect_brand(url, site_name)` fonksiyonudur. İleride AI tabanlı bir yöntem aynı imzayla bu fonksiyonun yerine konabilir; çözümleyiciye ve API'ye dokunmak gerekmez, `detect_brands` komutu mevcut kayıtları yeniden işaretler.

## API ayrıntıları

Token alma ve sayfa çekme komutları [README.md](README.md#api) dosyasındadır. Bir sayfanın tam cevabı:

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

## Ortam değişkenleri

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

## Ölçeklenme notu

Bu çözüm 4000 link için tek süreçte, yaklaşık 3-4 dakikada işini bitirir. Hacim büyürse (yüz binlerce link, sürekli akış) sırasıyla şunlar değişir:

- **Veritabanı: Postgres.** SQLite aynı anda tek yazara izin verir. Birden fazla işçi süreç aynı anda sonuç yazacaksa Postgres gerekir. Modeller ve sorgular standart Django ORM olduğu için geçiş yalnızca `DATABASES` ayarıdır; upsert (`bulk_create(update_conflicts=True)`) Postgres'te de aynı çalışır.
- **Kuyruk ve işçiler: Celery (veya benzeri bir kuyruk).** `resolve_links` bugün elle çalışan tek bir komuttur. Büyük hacimde linkler kuyruğa yazılır, birden fazla işçi kuyruktan okur; yeni link geldikçe otomatik çözülür, çöken işçinin işi başkasına geçer. `LinkResolver` sınıfı Django komutundan bağımsızdır, bir Celery görevi içinden aynen çağrılabilir.
- **Batch işleme.** Bugün "çözülecek linkler" tek seferde belleğe alınır; 4000 satırda sorun değildir. Milyonlarca satırda linkler sayfa sayfa (ör. 10.000'lik gruplar) okunmalı ve her grup ayrı bir iş olmalıdır. Sonuçların 500'lük gruplarla yazılması zaten bu yapıya uygundur.
- **Domain başına hız sınırı ortak tutulmalı.** Bugünkü limit tek sürecin içindedir. Birden fazla işçi olunca aynı siteye toplamda kaç istek atıldığını ortak bir yerde (ör. Redis) saymak gerekir; yoksa işçi sayısı arttıkça 429 ve engellemeler artar.
- **Çıkış IP'si.** [RESULTS.md](RESULTS.md) dosyasındaki sonuçların gösterdiği gibi, aynı IP'den tekrar tekrar tarama engellenmeyi artırır. Büyük hacimde istekler zamana yayılmalı, tekrar çözümleme aralığı (`resolved_at` üzerinden) planlanmalıdır.
- **API.** 1000'lik sayfa `count` sorgusu ve `OFFSET` kullanır; milyonlarca kayıtta derin sayfalar yavaşlar. O noktada `id` üzerinden ilerleyen (keyset/cursor) sayfalama tercih edilir.

Bunların hiçbiri bu görevin ölçeğinde gerekli değildir; bu yüzden projede Celery, Redis veya Postgres yoktur.
