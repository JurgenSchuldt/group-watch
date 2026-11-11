# Group Watch: evaluación por lotes

Casos válidos: **100** (fallidos: 0). Puntuaciones 0-100 de jueces LLM (Nova Pro) sobre perfiles sintéticos: una estimación, no opinión de espectadores reales.

## Resumen

| Sistema | Media de medias (top-1) | Media del peor espectador | Media de ajuste (fit) |
|---|---|---|---|
| Group Watch | 63.1 | 51.3 | 57.2 |
| IMDb | 54.5 | 40.4 | 47.5 |
| TF-IDF | 52.6 | 37.5 | 45.1 |

**Group Watch es el mejor en 53% de los casos** (53/100); contando empates técnicos (diferencia de fit ≤ 3): **64%** (64/100).

## Casos

| # | Espectadores | Elegida | GW media | IMDb media | TF-IDF media | Mejor |
|---|---|---|---|---|---|---|
| 1 | Federico Marino, Sara Greco, Hugo Marchand, Emma Carter | Pawn Sacrifice | 56 | 76 | 45 | IMDb |
| 2 | Julieta Maldonado, Hugo Castro | Heroic Losers | 55 | 50 | 48 | Group Watch (empate) |
| 3 | Lea Vogel, Jiwoo Kang, Emma Carter, Derek Foster | Icarus | 71 | 70 | 62 | Group Watch |
| 4 | Franco Roldan, Paige Whitfield, Clara Hoffmann, Olivia Sullivan | Fighting with My Family | 56 | 58 | 58 | Group Watch |
| 5 | Paige Whitfield, Davide Moretti | Paan Singh Tomar | 45 | 25 | 60 | TF-IDF |
| 6 | Larissa Teixeira, Freddie Hewitt | Fyre | 62 | 20 | 55 | Group Watch |
| 7 | Giulia Ferrari, Mariana Pacheco | The Intouchables | 70 | 70 | 52 | Group Watch |
| 8 | Maya Lowe, Harry Beckett, Julien Lefevre, Brandon Bennett | Green Book | 65 | 69 | 65 | Group Watch |
| 9 | Tobias Schreiber, Olivia Sullivan | Soul | 85 | 72 | 78 | Group Watch |
| 10 | Niklas Hoffmann, Tobias Vogel, Joaquin Castillo | Icarus | 77 | 23 | 37 | Group Watch |
| 11 | Clara Hoffmann, Brandon Bennett | Spirited Away | 60 | 68 | 72 | TF-IDF |
| 12 | Pilar Sosa, Davide Moretti, Hugo Marchand | Green Street Hooligans | 53 | 60 | 27 | Group Watch |
| 13 | Paige Whitfield, Lea Vogel, Maximilian Wagner | Schumacher | 47 | 50 | 50 | TF-IDF (empate) |
| 14 | Ava Bennett, Lukas Krause | Modern Times | 55 | 45 | 50 | Group Watch |
| 15 | Regina Navarro, Freddie Hewitt, Tobias Vogel | The Big Short | 77 | 42 | 57 | Group Watch |
| 16 | Harry Hollis, Riku Tanaka | Kiki's Delivery Service | 70 | 52 | 58 | Group Watch |
| 17 | Paige Whitfield, Sara Greco, Maximilian Wagner, Juliette Rousseau | Moneyball | 54 | 39 | 39 | Group Watch |
| 18 | Sakura Matsumoto, Rahul Rao, Yuna Lee, Manon Lemoine | The Pianist | 62 | 55 | 56 | Group Watch |
| 19 | Giulia Ferrari, Rahul Menon, Tobias Krause | Paprika | 52 | 69 | 50 | IMDb |
| 20 | Franco Roldan, Sungmin Yoon | Diego Maradona | 82 | 78 | 45 | Group Watch |
| 21 | Sakura Matsumoto, Yui Yamamoto | The Handmaiden | 72 | 25 | 55 | Group Watch |
| 22 | Hugo Castro, Lukas Krause | The Lives of Others | 68 | 68 | 30 | IMDb (empate) |
| 23 | Hugo Marchand, Maximilian Wagner | Ratatouille | 70 | 58 | 48 | Group Watch |
| 24 | Marie Keller, Yuna Lee, Manon Lemoine | The Triplets of Belleville | 48 | 53 | 47 | Group Watch (empate) |
| 25 | Ava Bennett, Marie Keller | The Lion King | 86 | 30 | 82 | Group Watch |
| 26 | Hugo Marchand, Sungmin Yoon | Parasite | 72 | 89 | 78 | IMDb |
| 27 | Lucia Bravo, Julien Lefevre, Jiwoo Kang, Juliette Rousseau | Diego Maradona | 64 | 71 | 52 | IMDb |
| 28 | Marco Vidal, Harry Beckett, Yuna Lee, Riku Tanaka | Se7en | 55 | 59 | 66 | IMDb |
| 29 | Marco Vidal, Harry Hollis | Clue | 55 | 60 | 45 | Group Watch (empate) |
| 30 | Hugo Marchand, Yuna Lee, Juliette Rousseau | High and Low | 73 | 52 | 65 | Group Watch |
| 31 | Hugo Castro, Madison Hayes, Mariana Pacheco, Jiwoo Kang | The Truman Show | 68 | 56 | 44 | Group Watch |
| 32 | Hugo Castro, Sota Yamamoto | Ghost in the Shell | 65 | 50 | 68 | TF-IDF |
| 33 | Madison Hayes, Tobias Vogel | WALL·E | 72 | 55 | 55 | Group Watch |
| 34 | Lucia Bravo, Davide Moretti, Hugo Marchand | The Invisible Guest | 58 | 55 | 40 | IMDb |
| 35 | Davide Moretti, Madison Hayes, Tobias Krause, Nana Ito | Knives Out | 70 | 51 | 41 | Group Watch |
| 36 | Sophie Wagner, Brandon Brooks, Nana Ito, Ximena Salas | Hidden Figures | 59 | 50 | 50 | Group Watch |
| 37 | Julieta Maldonado, Pilar Sosa, Regina Navarro, Harry Hollis | The Art of Racing in the Rain | 46 | 22 | 42 | Group Watch |
| 38 | Mariana Pacheco, Emma Carter | Exit Through the Gift Shop | 65 | 75 | 58 | IMDb |
| 39 | Regina Navarro, Clara Hoffmann, Olivia Sullivan, Emma Carter | As It Is in Heaven | 40 | 68 | 51 | IMDb |
| 40 | Julieta Maldonado, Hugo Castro, Marie Keller, Sophie Wagner | Spider-Man: Into the Spider-Verse | 59 | 54 | 28 | Group Watch (empate) |
| 41 | Franco Acosta, Paige Whitfield, Brandon Brooks, Jaewon Lee | Hoop Dreams | 66 | 65 | 58 | IMDb |
| 42 | Pilar Sosa, Yuna Lee | Rocco and His Brothers | 58 | 40 | 40 | Group Watch |
| 43 | Jiwoo Kang, Emma Renaud | 2001: A Space Odyssey | 65 | 78 | 65 | IMDb |
| 44 | Lucia Bravo, Maximilian Wagner, Freddie Hewitt | Fighting with My Family | 60 | 67 | 57 | Group Watch |
| 45 | Harry Hollis, Fernanda Nunes, Freddie Hewitt | Spider-Man: Into the Spider-Verse | 82 | 77 | 35 | Group Watch |
| 46 | Niklas Hoffmann, Mariana Pacheco, Joaquin Castillo | The Rescue | 47 | 37 | 30 | Group Watch |
| 47 | Sara Greco, Jiwoo Kang, Riku Tanaka, Nana Ito | Icarus | 74 | 78 | 49 | Group Watch |
| 48 | Hugo Marchand, Julien Lefevre, Jaewon Lee, Yui Yamamoto | Vertigo | 74 | 61 | 74 | TF-IDF (empate) |
| 49 | Sara Greco, Sakura Matsumoto, Clara Hoffmann | Wild Tales | 50 | 52 | 43 | IMDb |
| 50 | Niklas Hoffmann, Harry Hollis | Space Jam | 62 | 45 | 60 | Group Watch (empate) |
| 51 | Regina Navarro, Larissa Teixeira, Riku Tanaka, Nana Ito | The Imposter | 50 | 58 | 59 | TF-IDF |
| 52 | Larissa Teixeira, Ximena Salas | Exit Through the Gift Shop | 52 | 30 | 50 | TF-IDF |
| 53 | Yuna Lee, Joaquin Castillo | The Salt of the Earth | 70 | 40 | 50 | Group Watch |
| 54 | Larissa Teixeira, Yuna Lee, Manon Lemoine | The Social Network | 47 | 48 | 45 | IMDb (empate) |
| 55 | Pilar Sosa, Giulia Ferrari, Marie Keller, Jaewon Lee | The Pinchcliffe Grand Prix | 59 | 61 | 25 | IMDb (empate) |
| 56 | Harry Beckett, Yui Yamamoto | The Thin Blue Line | 58 | 75 | 60 | IMDb |
| 57 | Niklas Hoffmann, Madison Hayes, Freddie Hewitt, Ximena Salas | Eddie the Eagle | 58 | 55 | 50 | Group Watch |
| 58 | Federico Marino, Sara Greco, Juliette Rousseau | Senna | 62 | 28 | 68 | TF-IDF |
| 59 | Tobias Vogel, Manon Lemoine, Nana Ito | Citizenfour | 80 | 43 | 78 | TF-IDF (empate) |
| 60 | Franco Acosta, Hugo Renaud, Nana Ito | The Rescue | 57 | 52 | 43 | Group Watch (empate) |
| 61 | Paige Whitfield, Brandon Brooks, Riku Tanaka, Sota Yamamoto | Hoop Dreams | 75 | 66 | 50 | Group Watch |
| 62 | Harry Beckett, Jaewon Lee | Searching for Sugar Man | 85 | 82 | 62 | Group Watch |
| 63 | Julieta Maldonado, Sungmin Yoon | Diego Maradona | 72 | 60 | 45 | Group Watch |
| 64 | Marco Vidal, Regina Navarro, Mariana Pacheco | Wild Tales | 73 | 57 | 43 | Group Watch |
| 65 | Davide Moretti, Clara Hoffmann, Freddie Hewitt, Joaquin Castillo | The Truman Show | 68 | 42 | 48 | Group Watch |
| 66 | Giulia Ferrari, Brandon Bennett, Riku Tanaka, Jaewon Lee | High and Low | 79 | 66 | 78 | TF-IDF (empate) |
| 67 | Lucia Cordero, Sophie Wagner, Julien Lefevre, Sungmin Yoon | The Great Escape | 62 | 55 | 62 | Group Watch |
| 68 | Tobias Schreiber, Fernanda Nunes, Derek Foster | Inception | 63 | 68 | 68 | IMDb (empate) |
| 69 | Sara Greco, Hugo Castro, Hugo Renaud | The Usual Suspects | 73 | 58 | 53 | Group Watch |
| 70 | Federico Marino, Ruby Beckett | Eddie the Eagle | 50 | 50 | 52 | Group Watch |
| 71 | Niklas Hoffmann, Hugo Castro, Mariana Pacheco, Olivia Sullivan | Fyre | 60 | 28 | 52 | Group Watch |
| 72 | Yuna Lee, Tobias Krause | Ikiru | 72 | 75 | 72 | TF-IDF (empate) |
| 73 | Lucia Bravo, Hugo Castro | Heroic Losers | 60 | 50 | 40 | Group Watch |
| 74 | Ruby Beckett, Joaquin Castillo | Exit Through the Gift Shop | 55 | 50 | 40 | Group Watch |
| 75 | Hugo Renaud, Larissa Teixeira | Wild Tales | 68 | 62 | 35 | Group Watch |
| 76 | Davide Moretti, Brandon Brooks, Sungmin Yoon | Man on Wire | 65 | 50 | 58 | Group Watch |
| 77 | Julieta Maldonado, Riku Tanaka, Nana Ito, Juliette Rousseau | Icarus | 72 | 72 | 50 | Group Watch |
| 78 | Hugo Renaud, Claudia Carmona, Rahul Menon, Emma Renaud | The Rescue | 36 | 45 | 55 | TF-IDF |
| 79 | Giulia Ferrari, Maya Lowe, Tobias Vogel | The Truman Show | 55 | 50 | 55 | TF-IDF (empate) |
| 80 | Lea Vogel, Sungmin Yoon | Diego Maradona | 78 | 72 | 55 | Group Watch (empate) |
| 81 | Ava Bennett, Maya Lowe, Clara Hoffmann | Moana | 83 | 27 | 80 | Group Watch (empate) |
| 82 | Franco Acosta, Riku Tanaka, Joaquin Castillo | Senna | 77 | 68 | 37 | Group Watch |
| 83 | Franco Acosta, Lea Vogel, Sophie Wagner, Julien Lefevre | Champions | 48 | 30 | 42 | Group Watch |
| 84 | Lea Vogel, Ruby Beckett, Tobias Vogel, Joaquin Castillo | My Octopus Teacher | 45 | 42 | 40 | Group Watch (empate) |
| 85 | Giulia Ferrari, Harry Beckett | Whiplash | 82 | 82 | 52 | IMDb (empate) |
| 86 | Julieta Maldonado, Pablo Pascual, Clara Hoffmann | Wild Tales | 50 | 60 | 45 | IMDb |
| 87 | Hugo Renaud, Harry Hollis, Joaquin Castillo | Pom Poko | 48 | 53 | 47 | IMDb (empate) |
| 88 | Tobias Schreiber, Tobias Vogel | The Iron Giant | 55 | 50 | 50 | TF-IDF (empate) |
| 89 | Franco Acosta, Lea Vogel, Harry Beckett, Julien Lefevre | Ford v Ferrari | 62 | 22 | 64 | TF-IDF (empate) |
| 90 | Paige Whitfield, Juliette Rousseau | Icarus | 80 | 25 | 80 | Group Watch (empate) |
| 91 | Marco Vidal, Maya Lowe | The Way Way Back | 60 | 40 | 40 | Group Watch |
| 92 | Davide Moretti, Claudia Carmona, Joaquin Castillo | The Imposter | 57 | 50 | 53 | Group Watch |
| 93 | Franco Acosta, Marco Vidal, Derek Foster | Icarus | 68 | 63 | 68 | Group Watch (empate) |
| 94 | Niklas Hoffmann, Regina Navarro, Maximilian Wagner | Good Bye Lenin! | 48 | 73 | 47 | IMDb |
| 95 | Sakura Matsumoto, Clara Hoffmann, Brandon Bennett | Spirited Away | 67 | 50 | 48 | Group Watch |
| 96 | Mariana Pacheco, Sungmin Yoon, Tobias Krause, Emma Renaud | Three Identical Strangers | 59 | 59 | 50 | IMDb (empate) |
| 97 | Pilar Sosa, Giulia Ferrari, Sakura Matsumoto | Montevideo: Taste of a Dream | 60 | 67 | 40 | Group Watch |
| 98 | Franco Acosta, Olivia Sullivan | Icarus | 65 | 52 | 52 | Group Watch |
| 99 | Franco Acosta, Madison Hayes, Harry Beckett, Emma Carter | Eddie the Eagle | 58 | 64 | 64 | Group Watch |
| 100 | Larissa Teixeira, Lukas Krause, Ximena Salas | The Social Dilemma | 70 | 30 | 43 | Group Watch |
