Poniżej komplet promptów. Wklejasz je agentowi kodującemu na początku sesji. Wszystko, co zmienia się między sesjami, to numer (S07 itd.).

0. Przygotowanie (raz, przed S00)

Wyeksportuj dokument do Markdown i zapisz jako docs/SPEC.md w pustym repozytorium (git init). Nagłówki sesji muszą mieć postać ### S05 — ..., bo po nich tnie je skrypt z S00.

1. Prompt dla sesji S00 (start od zera)
Pracujesz nad projektem gpwbot: aplikacją w Pythonie typującą akcje na GPW
z użyciem lokalnego LLM. Pełna specyfikacja jest w docs/SPEC.md.

To jest sesja S00 (szkielet projektu). Repozytorium jest puste poza docs/SPEC.md.

Przeczytaj z docs/SPEC.md tylko:
- Część I: sekcje 3, 4 i 5,
- Część II: sekcję A (zasady pracy, A1–A6) oraz sekcję S00.

Zaimplementuj dokładnie zakres S00 wraz z testami wymienionymi w tej sekcji.
Dodatkowo utwórz AGENTS.md (konwencje i komendy, max 150 linii, na podstawie A1–A4),
docs/PROGRESS.md (tabela S00–S15 ze statusami, S00 = done po zakończeniu),
docs/INTERFACES.md i docs/decisions.md. Uruchom scripts/split_spec.py,
aby wygenerować docs/sessions/Sxx.md.

Zasady:
- Nie czytaj niczego poza wskazanymi sekcjami. Nie implementuj nic z późniejszych sesji.
- Nie używaj sieci poza instalacją zależności.
- Jeśli coś w specyfikacji jest niejednoznaczne, wybierz najprostsze rozwiązanie
  i zapisz decyzję w docs/decisions.md.
- Gdy zużyjesz ok. 70% kontekstu, a zadanie nie jest skończone, zatrzymaj się
  w stabilnym punkcie (testy zielone), zapisz stan w docs/PROGRESS.md i zakończ.

Na końcu: uruchom scripts/check.sh (ruff, mypy, pytest), zaktualizuj
docs/INTERFACES.md i docs/PROGRESS.md, zrób commit "S00: szkielet projektu".

Odpowiedz krótkim raportem: co zrobiono, wynik check.sh, odstępstwa od specyfikacji,
pytania otwarte.
2. Prompt dla sesji S01–S15 (uniwersalny)

Zmieniasz tylko numer sesji.

Pracujesz nad projektem gpwbot (aplikacja w Pythonie typująca akcje na GPW
z lokalnym LLM). To jest sesja S07.

Na początku przeczytaj wyłącznie:
1. AGENTS.md
2. docs/PROGRESS.md
3. docs/INTERFACES.md
4. docs/sessions/S07.md (zakres, specyfikacja, testy, akceptacja)
5. pliki źródłowe wymienione w polu "Wejścia" sesji, i tylko jeśli
   INTERFACES.md nie wystarcza.

Zaimplementuj dokładnie zakres S07 wraz z KAŻDYM testem wymienionym w sekcji Testy.
Najpierw napisz testy dla czystej logiki, potem implementację, potem testy integracyjne.

Zasady:
- Nie czytaj całego repozytorium i nie implementuj rzeczy z innych sesji.
- Nie zmieniaj publicznych sygnatur z wcześniejszych sesji. Jeśli musisz,
  wpisz uzasadnienie do docs/decisions.md i zaktualizuj wszystkie użycia oraz testy.
- Wszystkie parametry rynkowe i limity pochodzą z konfiguracji, nie z kodu.
- Wyniki poleceń ograniczaj (pytest -q -x --tb=short, head, tail, grep).
- Elementów, których nie da się zweryfikować (źródła danych, parametry GPW,
  flagi serwera LLM), nie zgaduj: zapisz w docs/decisions.md jako "do weryfikacji"
  i zapytaj mnie w raporcie.
- Gdy zużyjesz ok. 70% kontekstu, a zadanie nie jest skończone, zatrzymaj się
  w stabilnym punkcie (testy zielone), opisz stan w docs/PROGRESS.md i zakończ.

Na końcu: scripts/check.sh musi przejść, zaktualizuj docs/INTERFACES.md
(nowe publiczne sygnatury) i docs/PROGRESS.md (status, decyzje, dług techniczny),
zrób commit "S07: krótki opis".

Odpowiedz krótkim raportem: co zrobiono, wynik check.sh i pokrycie, odstępstwa
od specyfikacji, pytania otwarte, a w ostatniej linii: gotowość do sesji S08 (tak/nie).
3. Prompt kontynuacji przerwanej sesji

Użyj go, gdy agent zatrzymał się przed końcem zakresu albo zabrakło kontekstu.

Kontynuujesz sesję S07 projektu gpwbot, która została przerwana przed ukończeniem.

Przeczytaj: AGENTS.md, docs/PROGRESS.md (sekcja o S07 opisuje, co jest zrobione),
docs/INTERFACES.md oraz docs/sessions/S07.md. Uruchom `git log -5 --oneline`
i `git status`, a potem `pytest -q -x --tb=short`.

Ustal, które punkty zakresu i które testy z docs/sessions/S07.md nadal brakują,
i zaimplementuj tylko je. Nie przepisuj działającego kodu. Obowiązują te same zasady
co w uniwersalnym prompcie sesji (budżet kontekstu, brak zgadywania, check.sh,
aktualizacja INTERFACES.md i PROGRESS.md, commit).

Zakończ raportem: co dokończono, co zostało, czy sesja S07 spełnia kryterium akceptacji.
4. Prompt przeglądu po sesji (osobna, krótka rozmowa)

Warto użyć go po sesjach S06–S09 i S12, bo tam błędy kosztują najwięcej. Najlepiej w świeżym kontekście, a jeśli możesz, innym modelem.

Zrób przegląd kodu sesji S08 projektu gpwbot. Nie implementuj nowych funkcji.

Przeczytaj docs/sessions/S08.md oraz kod z ostatniego commitu (git show --stat HEAD,
potem pliki z src/gpwbot/risk/ i tests/risk/).

Sprawdź:
1. Czy każdy punkt specyfikacji i każdy test z sekcji Testy ma realizację w kodzie.
2. Błędy logiczne: zaokrąglenia (Decimal, tick), granice nierówności, kolejność reguł,
   nieobsłużone przypadki (pusta lista, None, zerowa wartość).
3. Look-ahead (użycie danych późniejszych niż as_of), czas bez strefy, float tam,
   gdzie powinien być Decimal.
4. Testy, które nic nie sprawdzają (brak asercji, zbyt słabe asercje), i brakujące
   przypadki brzegowe.

Zwróć listę znalezionych problemów z priorytetem (krytyczny / ważny / drobny),
plikiem i linią oraz proponowaną poprawką. Nie zmieniaj kodu, dopóki nie poproszę.
5. Prompt naprawczy (gdy testy są czerwone albo agent się zapętlił)
Stan repozytorium gpwbot jest niespójny: testy są czerwone lub poprzednia sesja
zakończyła się w połowie. Nie dodawaj nowych funkcji.

1. Uruchom `git status`, `git log -5 --oneline`, a potem `pytest -q --tb=short | tail -40`.
2. Zidentyfikuj pierwotną przyczynę (nie objaw). Napraw ją minimalną zmianą.
3. Jeśli test jest błędny względem docs/sessions/Sxx.md, popraw test i zapisz
   uzasadnienie w docs/decisions.md. Jeśli błędny jest kod, popraw kod.
4. Nie usuwaj ani nie osłabiaj testów, żeby przeszły. Nie używaj xfail bez
   uzasadnienia w docs/PROGRESS.md.
5. Powtarzaj, aż scripts/check.sh przejdzie, następnie commit "fix: opis".

Zakończ raportem: przyczyna, co zmieniono, czy któraś sesja wymaga ponownego przeglądu.
Uwagi praktyczne
Sesje z weryfikacją zewnętrzną (S02, S03, S04, S10, S11): jeśli Twój agent nie ma dostępu do internetu, dopisz do promptu: Nie masz dostępu do sieci, więc wartości do weryfikacji zostaw jako przykładowe, oznacz w decisions.md jako DO WERYFIKACJI i wypisz je w raporcie. Potem samodzielnie sprawdź święta GPW, tick size i adresy źródeł. Nie zostawiaj ich agentowi do zgadywania.
Testy llm_live (S11, S12) uruchamiasz sam przy działającym llama-server. Agent tego nie zrobi, więc dopisz: Testów oznaczonych llm_live nie uruchamiaj, przygotuj je tylko do ręcznego uruchomienia.
Po zmianie specyfikacji uruchom ponownie python scripts/split_spec.py, żeby docs/sessions/Sxx.md nie były nieaktualne.
Jedna sesja = jeden czysty kontekst. Nie kontynuuj kolejnej sesji w tej samej rozmowie, bo zużyje budżet 131 tys. tokenów i podważy cały podział.