# Sonuçlar ve sınırlamalar

Bu dosya [README.md](README.md) dosyasının devamıdır: örnek veride alınan sonuçlar, marka tespitinin ayrıntıları ve bilinen sınırlamaların uzun açıklamaları.

## Örnek veride sonuçlar

Aşağıdaki rakamlar 25 Kasım 2026'da temiz bir Docker ortamında (`docker compose up`, boş volume) alındı: 3305 benzersiz URL 208 saniyede çözüldü (yaklaşık 16 URL/sn, yeniden denemeler dahil).

### Durum dağılımı

| Status | Satır | Oran |
|---|---|---|
| `ok` | 1502 | %37,6 |
| `resolved` | 2401 | %60,0 |
| `blocked` | 79 | %2,0 |
| `error` | 18 | %0,5 |
| `timeout` | 0 | %0 |

Tablo, bu çalıştırmanın ve birkaç dakika sonra yapılan bir `resolve_links --retry-failed` çalıştırmasının ardından oluşan durumdur. İlk çalıştırma bittiğinde `blocked` 100 satırdı; 429 alan linklerin bir kısmı sonradan çözüldü.

4000 satırın 3907'sinde son URL bilinir (`link_detail` dolu); 93 satırda ilk istek başarısız olduğu için boştur.

### Marka oranları

`brand_exist`: 4000 satırın 690'ı (%17,3).

| Son URL | Satır | Marka bulunan | Oran |
|---|---|---|---|
| Trendyol | 2269 | 30 | %1,3 |
| Hepsiburada | 712 | 285 | %40,0 |
| Diğer siteler | 1019 | 375 | %36,8 |
| **Toplam** | 4000 | 690 | %17,3 |

### "Markanın kendi sitesi" kuralının etkisi

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

### Elle kontrol

**Elle kontrol:** 60 alan adlık örnekte (ilk 40 + rastgele 20) doğruluk %77–90 (6 yanlış, 8 belirsiz). Bu ölçüm düzeltmelerden önce yapıldı. Örnek, marka sayılan 153 alan adından satır sayısına göre ilk 40 ile kalanlardan rastgele seçilen 20 alan adıdır; kontrol sitelere girilmeden, yalnızca ada bakılarak yapıldı. Alt sınır belirsizleri yanlış, üst sınır doğru sayar. Bulunan 6 yanlış sonradan listeye işlendi (perakendeci veya "marka değil"); 8 belirsiz olduğu gibi bırakıldı.

### Çalıştırmalar arası fark

**Rakamlar çalıştırmadan çalıştırmaya değişir.** Siteler aynı IP'den gelen tekrarlı taramalara giderek daha sıkı davranır. Aynı gün yapılan daha önceki bir çalıştırmada dağılım `ok` 1679, `resolved` 2256, `blocked` 46 idi. Bir gün sonra (26 Kasım) repodan klonlanarak yapılan çalıştırmada `ok` 1068, `resolved` 2804, `blocked` 108, `error` 20 oldu: Hepsiburada bu kez 711 satırın 540'ında son sayfada 403 döndü. Son URL bu satırlarda yine kaydedildiği için marka oranı neredeyse aynı kaldı (690 → 680 satır, %17,3 → %17,0).

## Ürün bilgisi

26 Kasım 2026'da, aynı kayıtlar üzerinde. `extract_products` 4000 linkin 500'ünde ürün numarası buldu (8643 satır):

| Platform | Kaynak | Link | Satır | Farklı ürün |
|---|---|---|---|---|
| Trendyol | Ürün listesi (`/sr?id=...`) | 382 | 8525 | 4041 |
| Hepsiburada | Ürün sayfası | 38 | 38 | 29 |
| Amazon | Ürün sayfası | 29 | 29 | 26 |
| Trendyol | Ürün sayfası | 26 | 26 | 19 |
| Shopier | Ürün sayfası | 24 | 24 | 22 |
| Hepsiburada | Yalnızca zincirde | 1 | 1 | 1 |

Bir Trendyol ürün listesi ortanca 11, en çok 100 ürün taşır.

`fetch_products` Hepsiburada ve Shopier'deki 51 ürünü 51 saniyede okudu:

| Platform | Ürün | Ad okundu | Marka okundu | Bulunamadı |
|---|---|---|---|---|
| Hepsiburada | 29 | 28 | 28 | 1 |
| Shopier | 22 | 22 | 0 | 0 |

Hepsiburada'da ad ve marka JSON-LD'den gelir. Shopier sayfalarında JSON-LD yoktur; ad `og:title`'dan okunur, marka bilgisi sayfada bulunmaz. Bulunamayan ürün kaldırılmıştır (sayfa kategoriye yönlenir).

Ürün sayfasından okunan marka en öncelikli kaynak yapılınca `brand_exist` 690 satırdan 698'e çıktı (%17,3 → %17,5); artışın tamamı Hepsiburada'dadır (285 → 293, %40,0 → %41,2). Kazanılan 8 satırın markaları marka listesinde yoktu (OG natural, Porsima, Bloomie, Frankly, Oithox). Markası sayfadan okunan diğer 30 satırda URL kuralı zaten aynı markayı buluyordu; iki yöntem birbirini doğruladı. Bu belgenin başındaki tablolar bu adımdan önceki durumu gösterir.

Shopier mağaza kuralı (`shopier.com/{mağaza}/{ürün}` linkinde mağaza adı marka sayılır) 11 satır daha ekledi: 698 → 709 (%17,5 → %17,7). Veride altı mağaza vardır. Ürün adlarına bakılarak ikisi marka sayıldı (kendi ürününü satan `emekatolye` ve `siriusgaia`), dördü hariç tutuldu: başka bir markanın (Dilvin) ürünlerini satan bir butik perakendeci, kişilerin eğitim ve kulüp satışı yaptığı üç mağaza "marka değil" olarak listelendi. Ad mağazanın adresinden türetildiği için kabadır (`Emekatolye`).

Ürün adı kuralı (okunan adın başındaki listeli marka) 3 satır daha ekledi: 709 → 712 (%17,7 → %17,8). Üçü de perakendeci olarak hariç tutulan butiğin "Dilvin Marka … Kazak" ürünleridir; aynı butiğin marka adı taşımayan iki ürünü markasız kaldı.

Ürün adımlarının sonundaki durum:

| Son URL | Satır | Yalnızca URL'den | Ürün sayfalarıyla |
|---|---|---|---|
| Trendyol | 2269 | 30 (%1,3) | 38 (%1,7) |
| Hepsiburada | 712 | 285 (%40,0) | 293 (%41,2) |
| Diğer siteler | 1019 | 375 (%36,8) | 389 (%38,2) |
| **Toplam** | 4000 | 690 (%17,3) | 720 (%18,0) |

Trendyol satırı aşağıdaki 20 ürünlük denemeyi içerir; 4041 ürünün kalanı okunmadı.

`fetch_products` kesintiye karşı da sınandı: Shopier'deki 22 ürünün okunması 9. saniyede kesildi (8 ürün kaydedilmişti), aynı komut kalan 14 ürünle devam etti, üçüncü çalıştırma yapacak iş bulmadı.

**Trendyol: 20 ürünlük deneme.** İlk denemelerde Trendyol her isteği (ana sayfa dahil) 403 ile reddetti. Neden IP sanıldı, ama Türkiye çıkışlı bir proxy üzerinden de aynı cevap geldi. Fark protokolde çıktı: Trendyol bu istemcinin HTTP/2 isteklerini reddediyor, HTTP/1.1 isteklerine cevap veriyor; proxy'siz, aynı IP'den de. HTTP/1.1 ile 20 ürünün 20'si okundu (29 saniye, saniyede bir istek); 19'unda marka JSON-LD'den geldi, birinde sayfada JSON-LD yoktu ve ad `<title>`'dan alındı. Numaradan kurulan adres (`/x/x-p-{id}`) tek yönlendirmeyle ürünün kendi sayfasına gidiyor.

20 ürünün hepsi aynı satıcının 100 ürünlük listesindendi; 18'i Tchibo çıktı. Bu liste 8 içerikte paylaşıldığı için 8 satır marka kazandı: 712 → 720 (%17,8 → %18,0), Trendyol'da 30 → 38 (%1,3 → %1,7). Ürün numarası taşıyan 408 Trendyol satırı olduğu için tam taramanın getirebileceği en fazla artış bu kadardır (genel oranda en çok %28 dolayı).

**Tam tarama denemesi yarıda kaldı.** Saniyede bir istekle başlatılan tarama 74 istekten sonra durdu: 65 ürün okundu, 9'u reddedildi. Düzen iki kez aynıydı: yaklaşık 35 başarılı istekten sonra art arda retler; ikinci seferde 5 ret üst üste gelince komut kendiliğinden durdu. Yani Trendyol hızı da sınırlıyor; saniyede bir istek fazla. Okunan 65 ürün yine aynı 100 ürünlük Tchibo listesindendi (tarama tablo sırasıyla ilerliyordu), bu yüzden marka oranı değişmedi: 720 satır (%18,0). Toplam 85 Trendyol ürünü okundu, 80'inde marka var.

Bu denemeden sonra komut değişti: ret alınca 30 saniye bekler, ürünleri link link sırayla okur ve `--per-link` seçeneği vardır. `--per-link 3` ile okunması gereken ürün sayısı 3975'ten 479'a iner; her linkin markasına karar vermek için bu yeterlidir. Bu çalıştırma henüz yapılmadı.

## Mağaza sayfaları

26 Kasım 2026. Linklerde 598 farklı Trendyol satıcısı vardır ve 2153 satır onlara gider. `fetch_stores` 598 mağaza sayfasının 598'ini okudu: 2,5 saniye arayla, HTTP/1.1 üzerinden, 34 dakikada, hiç ret almadan.

| Son URL | Satır | Önce | Sonra |
|---|---|---|---|
| Trendyol | 2269 | 38 (%1,7) | 1997 (%88) |
| Hepsiburada | 712 | 293 (%41,2) | 293 (%41,2) |
| Diğer siteler | 1019 | 389 (%38,2) | 392 (%38,5) |
| **Toplam** | 4000 | 720 (%18,0) | 2682 (%67,1) |

Daha önce marka bulunan hiçbir satırın markası değişmedi; mağaza markası yalnızca boş olanları doldurur. "Diğer siteler"deki 3 satır, son URL'si Trendyol olmayan ama zincirinde satıcı numarası taşıyan linklerdir.

Bu ilk çalıştırmada üç kural vardı; mağazaların hangi kuralla marka sayıldığı:

| Kural | Mağaza | Link satırı |
|---|---|---|
| Ad marka listesinde (`listed`) | 65 | 834 |
| Ürünleri tek marka, mağaza o adla başlıyor (`products`) | 383 | 985 |
| Yalnızca onaylı satıcı işareti (`verified`) | 68 | 169 |
| Marka sayılmadı | 82 | 165 |

598 mağazanın 395'i onaylı satıcıdır.

### Elle kontrol ve kuralın değişmesi

30 mağazalık örnek elle kontrol edildi (bu ölçüm aşağıdaki değişiklikten önce yapıldı):

- **Marka sayılan 15 mağazada** (her kuraldan 5'er, rastgele) 2 yanlış çıktı: doğruluk yaklaşık %87. İkisi de yalnızca onaylı satıcı işaretiyle sayılmıştı: birkaç markayı satan `boomglobal` ve başka bir markanın ürünlerini satan `Çiçekburada`.
- **Marka sayılmayan 15 mağazada** (en çok link alan 8 + rastgele 7) 4 kaçan marka vardı: `StopForte`, `FRESH SCARFS`, `Kyza`, `VAGON TOYS`. Dördü de kendi markasını satıyor, ama mağaza adı ile ürünlerdeki marka farklı yazılmıştı.

Bunun üzerine kural değişti: onaylı satıcı işareti kuraldan çıkarıldı, `products` kuralındaki ad karşılaştırması esnek hale getirildi (büyük/küçük harf, boşluk, noktalama, Türkçe karakter farkı sayılmaz; adlardan biri diğerini içerebilir).

| Son URL | Satır | Üç kuralla | İki kuralla |
|---|---|---|---|
| Trendyol | 2269 | 1997 (%88) | 1919 (%85) |
| Hepsiburada | 712 | 293 (%41,2) | 293 (%41,2) |
| Diğer siteler | 1019 | 392 (%38,5) | 390 (%38,3) |
| **Toplam** | 4000 | 2682 (%67,1) | 2602 (%65,1) |

| Kural | Mağaza | Link satırı |
|---|---|---|
| Ad marka listesinde (`listed`) | 65 | 834 |
| Ürünleri tek marka, mağaza aynı adı taşıyor (`products`) | 427 | 1070 |
| Marka sayılmadı | 106 | 249 |

- **İşaret kalkınca 43 mağaza (125 satır) düştü.** Çoğu çok markalı satıcıdır ve düşmesi doğrudur: `P&G Türkiye`, `SaatveSaat`, `ebebek`, `Watsons`, `Rossmann`, `Unilever`, `SuperStep`, `A101 Ekstra`, `Trendyol`. Aralarında markanın kendi mağazası olup adı ürün markasıyla eşleşmeyenler de vardır: `ARMANI BEAUTY` (ürünlerde "Giorgio Armani", 12 satır), `Katia and Bony` (ürünlerde "Katia&Bony", 7 satır), `Marks and Spencer` (ürünlerde "Marks & Spencer").
- **Esnek eşleşmeyle 44 mağaza (85 satır) geldi:** 19'u daha önce hiç sayılmıyordu (`StopForte`, `FRESH SCARFS`, `Kyza`, `VAGON TOYS` dahil), 25'i yalnızca işaretle sayılıyordu ve artık ürün markasının adıyla yazılıyor (`SkinSensual` → Skin Sensual, `Civil` → Civil Baby).
- **`boomglobal` yeniden sayılıyor, bu kez "BOOM" olarak:** ürünlerinin çoğu BOOM markasıdır ve mağaza adı bu markayı içerir. İlk kontrolde yanlış sayılmasının nedeni mağaza adının marka diye yazılmasıydı; ürün markasının adıyla yazılınca doğrudur.

### Son hali

İki küçük düzeltme daha yapıldı: ad karşılaştırmasında "and", "ve" ve "&" aynı sayılıyor (`Katia and Bony`, `Marks and Spencer`), ve ürünleri "Giorgio Armani" adıyla satılan `ARMANI BEAUTY` için marka listesine Armani eklendi. 21 satır daha marka kazandı; başka hiçbir satır değişmedi.

| Son URL | Satır | Yalnızca URL'den | Son hali |
|---|---|---|---|
| Trendyol | 2269 | 30 (%1,3) | 1940 (%85,5) |
| Hepsiburada | 712 | 285 (%40,0) | 293 (%41,2) |
| Diğer siteler | 1019 | 375 (%36,8) | 390 (%38,3) |
| **Toplam** | 4000 | 690 (%17,3) | 2623 (%65,6) |

| Mağaza kuralı | Mağaza | Link satırı |
|---|---|---|
| Ad marka listesinde (`listed`) | 66 | 846 |
| Ürünleri tek marka, mağaza aynı adı taşıyor (`products`) | 429 | 1079 |
| Marka sayılmadı | 103 | 228 |

Bu son hal ayrıca elle doğrulanmadı; yukarıdaki %87'lik ölçüm ilk, üç kurallı sürüme aittir.

## Marka tespiti ayrıntıları

Kuralların kendisi [DESIGN.md](DESIGN.md#marka-tespiti) dosyasındadır.

**Marka olmayan alan adları da ayrı bir dosyadadır: `links/non_brand_domains.txt`.** Bölümleri: pazaryeri, çok markalı perakendeci (Boyner, CarrefourSA, iHerb), sosyal medya, haber, link kısaltıcı, link-in-bio, platform (bilet, form, müzik, site kurucu), kurum (`.org`, `.gov.tr`, `.edu.tr`), bahis, elle kontrol edilip marka olmadığı görülen siteler ve "belirsiz". "Belirsiz" bölümünde örnek veride görülen, adından ve URL'sinden ne olduğu anlaşılmayan siteler durur; biri kontrol edene kadar marka sayılmazlar. Perakendecide satılan bir marka URL'de geçiyorsa yine sayılır (`boyner.com.tr/kiko-milano-...` → `KIKO Milano`). Bir satır bir alan adı (`youtube.com`, alt alan adlarıyla), bir uzantı (`org`) veya her uzantıdaki bir ad (`amazon.*`) olabilir. Dosyaya satır eklemek kod değişikliği gerektirmez.

4\. kuralda marka adı iki kaynaktan gelir:

- **`og:site_name`:** çözümleme sırasında son sayfanın zaten okunan ilk 64 KB'ında bu etiket varsa değeri zincirin son adımına `site_name` olarak yazılır (ek istek yoktur). 40 karakterden uzun (slogan) veya alan adının kendisi olan değerler kullanılmaz.
- **Alan adı:** `site_name` yoksa ad alan adından türetilir; alt alan adı ve uzantı (`com.tr`, `co.uk`) atılır. Ad marka listesinde varsa listedeki yazımı kullanılır (`www2.hm.com` → `H&M`).

## Sınırlamaların ayrıntısı

- **Trendyol'da marka adı URL'de yok, yalnızca ID var.** Trendyol kısa linklerinin yaklaşık %96'sı ürün sayfasına değil, satıcı mağazasına (`/magaza/x-m-123456`) veya ID'lerle tanımlı bir aramaya (`/sr?mid=...`) gider. İlk çözümde bu sayfalar okunamadı (HTTP/2 isteklerine 403) ve oran %1,3'te kaldı. Sayfaların HTTP/1.1 ile okunabildiği görülünce satıcı adı mağaza sayfasından alındı (bkz. "Mağaza sayfaları"); oran %85'e çıktı. Kalan sınırlar:
  - **Bulunan marka mağazanın markasıdır.** İçerikte gösterilen ürünün markası değil, linkin gittiği satıcının markası yazılır; çok markalı bir satıcıda bu ikisi farklı olabilir.
  - **Mağaza adı ile ürün markası eşleşmeyince marka kaçar.** `ARMANI BEAUTY` "Giorgio Armani" satar, `Katia and Bony` "Katia&Bony"; bunlar marka sayılmaz. Onaylı satıcı işareti bu boşluğu doldurmak için kullanılamaz, çünkü çok markalı yetkili satıcılara da verilir.
  - **Marka ID'si (`BrandIds`)** 2266 Trendyol satırının 75'inde doludur ve `marketplace_brand_id` alanında saklanır; bir ID→isim tablosu olmadığı için kullanılmaz.
- **Marka oranları iyimserdir.** Marka listesi (`links/brands.txt`) bu örnek veride görülen markalarla genişletildi. Daha önce görülmemiş linklerde isabet oranı daha düşük olacaktır; liste elle bakım ister.
- **"Markanın kendi sitesi" bir varsayımdır.** Listede olmayan her site marka sayılır; yani kural hariç tutulanlar listesi kadar doğrudur. Liste örnek verideki alan adlarından ve bilinen sitelerden derlendi; siteler tek tek ziyaret edilmedi, adlarına bakılarak sınıflandırıldı. Yeni veride listede olmayan bir haber sitesi veya platform yanlışlıkla marka sayılır. Listede olmayan kişisel siteler (bir doktorun, bir eğitmenin, bir sanatçının sitesi) de marka sayılır. Çok markalı perakendeciler ve mağaza zincirleri (Sephora, Gratis, Watsons) sayılmaz. Elle kontrol edilen 60 alan adlık örnekte doğruluk %77–90 çıktı (bkz. yukarıda "Elle kontrol"); ölçüm yalnızca ada bakılarak yapıldı ve kontrol edilmeyen 93 alan adını kapsamaz. Bu yüzden %17,3 bir üst sınır gibi okunmalıdır.
- **Alan adından türetilen ad kabadır.** Kelimeler ayrılamaz ve Türkçe karakterler geri gelmez: `sedayalcinatelier.com` → `Sedayalcinatelier`. `og:site_name` bunu düzeltir, ama örnek verideki sitelerde nadiren vardır: denenen 14 gerçek sayfanın 3'ünde bulundu, yalnızca 1'i kullanılabilirdi (biri slogan, biri alan adının kendisiydi). Sayfa HTML'i saklanmadığı için mevcut kayıtlarda `site_name` yoktur; bu kayıtlarda ad alan adından gelir. `site_name` bir sonraki `resolve_links` çalıştırmasında dolar.
- **Bot koruması.** Trendyol son sayfada 403 döner (`resolved`: son URL yine de bilinir). 79 satır ilk istekte reddedildi (`blocked`): 46'sı 401/403 (Passo, Sephora, lnk.bio gibi siteler), 33'ü 429. Koruma aşılmaya çalışılmaz.
- **Hız sınırı (429).** Bazı siteler aynı IP'den kısa sürede gelen istekleri sınırlar ve 60 saniye beklenmesini ister. Komut bu kadar beklemez (bekleme en fazla 10 sn); sonda bir kez daha dener, yine 429 alırsa satır `blocked` kalır. Bu satırlar bir süre sonra `resolve_links --retry-failed` ile yeniden denenebilir.
- **JS ile yapılan yönlendirmeler.** Tarayıcı çalıştırılmaz. Yalnızca `meta refresh` ve küçük sayfalardaki düz `location = "..."` kalıpları yakalanır; karmaşık JS yönlendirmeleri kaçar.
- **Geçici hatalar.** İlk geçişte 43 URL zaman aşımı veya hata ile bitti; komutun sonundaki otomatik ikinci denemede 25'i çözüldü. Kalan 18 `error` kalıcıdır (ölü linkler, WhatsApp'ın 400 döndüğü linkler, bozuk URL'ler).
- **HTTPS yok.** Servis düz HTTP ile çalışır; bu yerel kullanım içindir. Production'da önüne TLS sonlandıran bir proxy konur ve Django'nun güvenli çerez/HSTS ayarları açılır.
- **Sonuç ana bağlıdır.** Kısa linkler zamanla başka yere yönlenebilir veya silinebilir; `resolved_at` bu yüzden tutulur.
