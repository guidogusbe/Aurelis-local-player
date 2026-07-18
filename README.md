# Aurelis

Aurelis è un lettore musicale offline per la libreria musicale locale. Scansiona una cartella sul computer, estrae i metadati (titolo, artista, album, copertina) e permette di sfogliare e riprodurre i brani tramite un'interfaccia scura.

Nessuno streaming, nessun account: la musica resta salvata sul disco dell'utente.

## Funzionalità

- Scansione automatica di una cartella musicale (ricorsiva), con estrazione dei metadati via mutagen
- Libreria con tabella ricercabile (titolo, artista, album, durata)
- Player con play/pausa, brano successivo/precedente, slider di posizione e volume
- Preferiti per contrassegnare i brani
- Interfaccia scura costruita con PySide6 e QSS personalizzato
- Scansione e riproduzione eseguite in background (QThread), senza bloccare l'interfaccia

## Stack tecnologico

| Componente | Tecnologia |
|---|---|
| Linguaggio | Python 3.13+ |
| Interfaccia grafica | PySide6 (Qt for Python) |
| Database | SQLite3 |
| Metadati audio | Mutagen |
| Riproduzione audio | QtMultimedia (QMediaPlayer / QAudioOutput) |

## Struttura del progetto

```
Aurelis/
├── main.py                    # Entry point dell'applicazione
├── requirements.txt           # Dipendenze del progetto
├── database/                  # Database SQLite (non versionato, vedi .gitignore)
├── config/                    # Impostazioni utente (non versionato)
├── cache/thumbnails/          # Copertine ridimensionate (non versionato)
├── ui/                        # Interfaccia grafica (PySide6)
│   ├── main_window.py         # Finestra principale
│   ├── player_bar.py          # Barra di riproduzione inferiore
│   ├── library_page.py        # Pagina libreria musicale
│   └── widgets/                # Widget riutilizzabili
├── player/                    # Logica di riproduzione e scansione
│   ├── audio_engine.py        # Motore di riproduzione (QtMultimedia)
│   ├── scanner.py             # Scanner in background delle cartelle
│   └── metadata.py            # Estrazione tag con Mutagen
└── database_manager/          # Query e gestione del database SQLite
    ├── database.py             # Connessione e inizializzazione tabelle
    └── songs.py                 # Query per inserimento/recupero brani
```

## Installazione

Requisiti: Python 3.13 o superiore.

```bash
git clone https://github.com/<tuo-username>/Aurelis.git
cd Aurelis
pip install -r requirements.txt
python main.py
```

Al primo avvio l'app crea le cartelle necessarie (database/, cache/thumbnails/) e chiede di selezionare la cartella musicale da scansionare.

## Utilizzo

1. Avvia l'app con `python main.py`
2. Clicca su "Carica cartella" e seleziona la cartella con la musica
3. Attendi il completamento della scansione (avviene in background)
4. Sfoglia la libreria, cerca un brano, fai doppio click per riprodurlo
5. Usa la barra in basso per controllare riproduzione, volume e avanzamento

Se i file musicali vengono spostati o rinominati dopo la scansione, l'app potrebbe non trovarli più, perché il percorso viene salvato al momento della scansione. Evitare di spostare i file dopo l'importazione, oppure ripetere la scansione.

## Dati locali e privacy

Il repository non contiene la musica, il database o le impostazioni personali dell'utente — sono esclusi tramite .gitignore:

- database/*.db — il database con la libreria
- config/settings.json — le impostazioni personali
- cache/thumbnails/ — le copertine estratte
- eventuali file audio (.mp3, .flac, .wav, ecc.) presenti nel progetto

Se questi file sono già stati inclusi in un commit precedente, possono essere rimossi dal tracking (restano sul disco) con:

```bash
git rm -r --cached database/*.db cache/thumbnails
git commit -m "Remove local data from version control"
```

## Roadmap

- Equalizzatore audio
- Testi sincronizzati (.LRC)
- Playlist personalizzate con drag & drop
- Statistiche d'ascolto dettagliate
- Pulizia automatica dei brani con file mancante

## Licenza

Da definire. Per scegliere una licenza: choosealicense.com
