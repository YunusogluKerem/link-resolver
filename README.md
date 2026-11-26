# Link Çözümleme Servisi

Platformlarda paylaşılan linkleri (çoğu kısa link) son adresine kadar takip eder, yönlendirme zincirini saklar ve linkte bir ürün markası geçip geçmediğini işaretler.

Stack: Python 3.12, Django 5.2 (LTS), Django REST Framework, simplejwt, httpx (async, HTTP/2), openpyxl, SQLite, Docker.

## Özet

- **Veri:** 4000 satır, 3305 benzersiz URL; tamamı yaklaşık 3,5 dakikada çözülür.
- **Son URL:** satırların %98'inde bulunur (3907 / 4000).
- **Marka:** satırların %17,3'ünde tespit edilir. Hepsiburada'da %40, Trendyol'da %1,3; nedeni [Bilinen sınırlamalar](#bilinen-sınırlamalar) bölümündedir.
- **Kurulum:** `.env` ve Excel yerine konduktan sonra tek komut: `docker compose up -d`.
- **API:** yalnızca admin kullanıcıya açık, JWT ile; sayfa başına 1000 kayıt.

Tasarım kararları: [DESIGN.md](DESIGN.md). Sonuç tabloları ve sınırlamaların ayrıntısı: [RESULTS.md](RESULTS.md).

## Hızlı başlangıç (Docker)

```bash
# 1. Ayarlar: örnek dosyayı kopyalayın, anahtarı ve admin şifresini değiştirin
cp .env.example .env

# 2. Excel dosyasını data/ klasörüne koyun (repoda yoktur)
cp /path/to/dosya.xlsx data/links.xlsx

# 3. Servisi başlatın
docker compose up -d

# 4. Linkleri içeri alın ve çözün
docker compose exec web python manage.py import_links data/links.xlsx
docker compose exec web python manage.py resolve_links

# 5. Testler (ağa çıkmaz)
docker compose exec web python manage.py test links
```

API `http://localhost:8000/api/`, admin paneli `http://localhost:8000/admin/` adresindedir. Kullanım örnekleri için "API" bölümüne bakın.

Konteyner her açılışta migration'ları uygular ve `.env` içindeki admin kullanıcıyı yoksa oluşturur. Docker kararları ve Docker'sız kurulum: [DESIGN.md](DESIGN.md#docker-kararları).

## Komutlar

| Komut | Ne yapar |
|---|---|
| `import_links <dosya.xlsx>` | Excel'deki linkleri `Links` tablosuna alır. Tekrar çalıştırılabilir; kayıt çoğaltmaz. |
| `resolve_links` | Linkleri çözer, sonuçları `LinkDetails` tablosuna yazar. |
| `detect_brands` | Marka alanlarını ağa çıkmadan yeniden hesaplar (bkz. [DESIGN.md](DESIGN.md#marka-tespiti)). |
| `extract_products` | Linklerdeki ürün numaralarını `link_products` tablosuna çıkarır; ağa çıkmaz. |
| `fetch_products` | Ürün sayfalarından ürün adını ve markasını okur (Hepsiburada, Shopier; `--platform trendyol` ile Trendyol). |
| `ensure_admin` | Ortam değişkenlerinden admin kullanıcıyı yoksa oluşturur. |

`resolve_links` tekrar çalıştırılabilir: yalnızca henüz çözülmemiş linkleri işler, `--force` ile hepsini baştan çözer. Tüm seçenekler: [DESIGN.md](DESIGN.md#resolve_links-seçenekleri).

Ürün bilgisi için sıra: `extract_products`, `fetch_products`, sonra `detect_brands` (ürün sayfasından okunan marka linkin markası olur). `fetch_products` linkleri yeniden çözmez, site başına saniyede bir istek atar ve art arda 5 kez reddedilirse o siteyi bırakır.

**Trendyol notu:** Trendyol IP bazlı engel uygular; aynı IP'den yapılan yoğun taramadan sonra ana sayfa dahil her isteğe 403 döner. Komut engeli aşmaya çalışmaz. Engellenmemiş farklı bir ağdan (başka bir çıkış IP'si) çalıştırılabilir: `python manage.py fetch_products --platform trendyol --limit 20`. Ayrıntı: [DESIGN.md](DESIGN.md#ürünler).

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
      "link": "https://ty.gl/abc123?fbclid=...",
      "link_detail": "https://www.trendyol.com/magaza/x-m-123456?...",
      "brand_exist": false,
      "status": "resolved"
    }
  ]
}
```

Her kayıtta ayrıca `link_id`, `content_id`, `brand_name`, `marketplace_brand_id`, `status_code`, `redirect_chain` ve `resolved_at` bulunur. Son sayfada `remaining_pages` `0`, `next` `null` olur. Tam örnek ve notlar: [DESIGN.md](DESIGN.md#api-ayrıntıları).

Linklerdeki ürünler aynı biçimde sayfalanır: `GET /api/link-products/?page=N`, isteğe bağlı `&platform=trendyol`.

### Güvenlik notu: token ömrü

Görev gereği access token ömrü yaklaşık 4 yıldır (1460 gün). Sızan bir token süresi dolana kadar geçerlidir ve tek tek iptal edilemez; tek çare `DJANGO_SECRET_KEY`'i değiştirmektir. Production'da ömür `JWT_ACCESS_TOKEN_DAYS` ile kısaltılır ve token `POST /api/token/refresh/` ile yenilenir; kod değişikliği gerekmez.

## Bilinen sınırlamalar

- **Trendyol'da marka çoğunlukla bulunamaz.** Kısa linklerin yaklaşık %96'sı marka adı içermeyen satıcı mağazasına veya ID'li bir aramaya gittiği için oran %1,3'tür.
- **Marka oranları iyimserdir.** Marka listesi bu örnek veride görülen markalarla genişletildi; yeni veride isabet daha düşük olur.
- **"Markanın kendi sitesi" bir varsayımdır.** Listede olmayan her site marka sayılır; elle kontrol edilen 60 alan adında doğruluk %77–90 çıktı.
- **Alan adından türetilen ad kabadır.** Kelimeler ayrılamaz ve Türkçe karakterler geri gelmez (`sedayalcinatelier.com` → `Sedayalcinatelier`).
- **Bot koruması aşılmaya çalışılmaz.** Trendyol son sayfada 403 döner (son URL yine de bilinir), 79 satır ise ilk istekte reddedildi.
- **Hız sınırı (429).** Aynı IP'den gelen istekleri sınırlayan sitelerde satır `blocked` kalabilir; sonra `resolve_links --retry-failed` ile yeniden denenir.
- **JS yönlendirmeleri.** Tarayıcı çalıştırılmaz; yalnızca `meta refresh` ve basit `location = "..."` kalıpları yakalanır.
- **Kalıcı hatalar.** Otomatik ikinci denemeden sonra kalan 18 `error` ölü link veya bozuk URL'dir.
- **HTTPS yok.** Servis düz HTTP ile çalışır; production'da önüne TLS sonlandıran bir proxy konur.
- **Sonuç ana bağlıdır.** Kısa linkler zamanla değişir ve rakamlar çalıştırmadan çalıştırmaya oynar; `resolved_at` bu yüzden tutulur.

## Ayrıntılar

- [DESIGN.md](DESIGN.md): veri modeli, çözümlemenin işleyişi, marka tespiti kuralları, ürünler, Docker kararları, ortam değişkenleri, ölçeklenme notu.
- [RESULTS.md](RESULTS.md): örnek veride sonuçlar ve sınırlamaların uzun açıklamaları.
