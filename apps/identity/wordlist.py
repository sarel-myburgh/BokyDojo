"""Words for generated passphrases — TODO 0.6.8.

⚠ Embedded rather than pulled from a package or /usr/share/dict. The container
image must generate the same passphrases as a development checkout, and a
wordlist that varies by host is a temporary password an administrator reads out
and the recipient cannot type.

Chosen for being read aloud and typed by somebody who is not a typist:

* three to eight letters, lower-case, no accents or apostrophes;
* concrete and common — a word somebody can spell on first hearing;
* no near-homophones of each other where it could be avoided (no "flour"
  alongside "flower"), because these are dictated across a room;
* nothing that could read as an insult when three of them land side by side.

⚠ Length is what makes a passphrase strong, not obscurity. There are 1787 words
here, so four of them is 43 bits — behind a five-attempt lockout that is far more
than the job needs, and unlike the random-character version it can be read down a
phone line without a single "was that an l or a 1".
"""

from __future__ import annotations

_RAW = """
able acorn actor adapt add adobe adopt adult afford again agent agree ahead aim
air alarm album alert alike alive alley almond alone alpha also amber amend
amount ample anchor angle animal ankle answer antler anvil apart apple april
apron arcade arch arena argue arise armor army aroma arrow art ash aside ask
aspen asset atlas atom attic auburn audio audit august aunt author auto autumn
avenue avoid awake award aware away awning axis bacon badge bagel baker
balance ball balloon bamboo banana band banjo bank barley barn basil basin
basket bat batch beach beacon bead beam bean bear beard beaver bed beech beetle
begin behind bell belt bench bend berry beside best better beyond bicycle big
bike bill birch bird birth biscuit bison bit bitter black blade blanket blend
bless blind blink block bloom blossom blue blur board boat bobcat body boil
bold bolt bond bone bonus book boot border born borrow bottle bounce bound bow
bowl box brace braid brain branch brand brass brave bread break bream breeze
brick bridge brief bright bring brisk broad bronze brook broom brown brush
bubble bucket buckle buddy budget buffalo build bulb bulk bull bundle bunny
burden burn burst bus bush busy butter button buy cabin cable cactus cage cake
calm camel camera camp canal candle cane canoe canvas canyon cap cape captain
car carbon card cargo carpet carrot carry cart carve case cash castle cat cedar
ceiling cement cent center chair chalk change chapel charm chart chase cheese
cherry chess chest chief child chill chimney chip chorus cider cinema circle
citrus city civil claim clam clap class clay clean clear clever cliff climb
clock cloth cloud clover club clue coach coal coast coat cobalt cocoa coffee
coin cold collar colony color column comb combine come comet comfort common
compass cone copper coral cord cork corn corner cotton couch count county
courage course cousin cover cow coyote crab craft crane crate crayon cream
create creek crest crew cricket crimson crisp crop cross crowd crown cruise
crumb crystal cube cuff cup curl curtain curve cushion custom cycle daily dairy
daisy dance dandy dark dart dash data dawn day deal dear debate decade deck
decor deep deer defend degree delay delta demand denim dense dentist depend
depth desert design desk detail detect device dial diary diet dig digital
dinner direct dish disk ditch dive dock doctor dodge dog dollar dolphin dome
donate donkey door double dough dove down dozen draft dragon drain drama draw
dream dress drift drill drink drive drop drum dry duck due duet dune dusk dust
duty eagle early earn earth ease east easy eat echo edge edit effort egg eight
elbow elder electric elegant elk elm else ember embrace empty enable enact end
energy engine enjoy enough enter entire entry equal equip erase escape essay
estate ethics evening event ever every exact exam exceed excess exchange excite
exit expand expert explain export express extend extra eye fabric face fact
fade fair falcon fall family famous fan fancy far farm fast father fault favor
feast feather fee feed feel fence fern ferry fever fiber field fifth fig figure
file fill film filter final find fine finger finish fire firm first fish fist
fit five fix flag flame flash flat flavor fleet flesh flex flint float flock
flood floor flour flow fluid flute fly foam focus fog foil fold folk follow
food foot force forest forge fork form fort forum found four fox frame free
fresh friend frog front frost fruit fuel full fun fund fur future gadget gain
gallon game gap garage garden garlic gas gate gather gauge gear gem gentle
genuine ginger giraffe give glad glass glide globe gloss glove glow glue goal
goat gold golf good goose grace grade grain grand grant grape graph grasp grass
gravel gray great green greet grid grill grip grocery groove ground group grove
grow guard guess guest guide guitar gulf gym habit hair half hall hammer hand
handle happy harbor hard harvest hat hatch have hawk hazel head health heap
heart heat heavy hedge heel height helmet help herb here hero high hill hint
hip hire history hobby hockey hold hole hollow home honey honor hood hoof hook
hope horn horse hose host hotel hour house human humble hunt hurry ice icon
idea ideal image impact import inch income index indoor infant ingot inland
inner input insect inside insight instant invest invite iron island issue item
ivory jacket jade jam jar jazz jelly jet jewel job join joint joke journal
journey joy judge juice jump junior juniper just kayak keen keep kettle key
kick kind king kiosk kite kitten knee knife knock knot know lab label labor
lace ladder lake lamb lamp land lane lantern lap large laser last late laugh
launch laundry law layer lead leaf lean learn lease leather leave ledge left
legal legend lemon lend length lens lesson letter level lever liberty library
life lift light like lily lime limit line linen link lion liquid list listen
little live lizard load loaf loan lobby local lock lodge log logic long look
loop lord lose lotus loud love low loyal luck lucky lumber lunar lunch lung
lush luxury lyric machine magic magnet maid mail main major make mammal manage
mango manner map maple marble march margin marine market marsh mask mass master
match math matter maze meadow meal mean measure meat medal media medium meet
melody melon member memory mend menu mercy merge merit merry mesh metal meter
method middle might mild mile milk mill mind mine mineral mint minute mirror
mist mix mobile model modern modest moment money monitor monkey month moon
moral more morning most motion motor mount mouse mouth move movie much mud
muffin mule multi muscle museum music must mutual myth nail name napkin narrow
nation native nature navy near neat neck nectar need needle neon nerve nest net
never new news next nice night nine noble node noise noodle noon normal north
nose note notice novel number nurse nut oak oasis oat ocean octopus odd offer
office often oil olive omega onion only open opera opinion orange orbit orchard
order organ origin otter ounce outdoor outer output oval oven over owl own
oxygen oyster pace pack pad page paint pair palace pale palm panda panel panic
paper parade parcel parent park parrot part party pass past pasta patch path
patient patrol pattern pause pave peace peach peak peanut pear pearl pebble
pelican pen pencil penny people pepper perch perfect period permit person pet
petal phase phone photo phrase piano pick picture pie piece pigeon pile pilot
pin pine pink pioneer pipe pitch pixel pizza place plain plan plane plank plant
plastic plate play plaza pledge plenty plot plum plus pocket poem point polar
pole police polish pond pony pool poplar poppy port post pot potato pouch
pound powder power praise prawn pray precise prefer prepare present press
pretty price pride primary print prism prize problem produce profit project
promise proof proper protect proud prove public pull pulse pump pumpkin punch
pupil puppy pure purple purpose purse push puzzle quail quality quantum quarry
quarter queen quest quick quiet quilt quit quiz quote rabbit raccoon race rack
radar radio raft rail rain raise rally ramp ranch random range rank rapid rare
rate rather raven ray reach react read ready real reason rebel recall receive
recipe record recover red reduce reef refer reflect refuse regard region
regular relax relay release relief remain remind remote remove render renew
rent repair repeat replace reply report request rescue resist resort respect
rest result retain retire return reveal review reward rhythm ribbon rice rich
ride ridge rifle right rigid ring rinse ripe rise risk ritual river road roast
robin robot rock rocket rod role roll roof room root rope rose rough round
route royal rubber ruby rug rule run rural rush rust sad safe sage sail salad
salmon salon salt sample sand sardine sauce sausage save saw say scale scan
scarf scene scent school science scope score scout scrap screen script sea seal
search season seat second secret section secure seed seek seem select self sell
send senior sense sentence series serve session settle seven shade shadow shake
shallow shape share shark sharp shed sheep sheet shelf shell shield shift shine
ship shirt shock shoe shop shore short should shoulder shout show shower
shuffle side siege sight sign silent silk silver simple since sing single sink
siren sister site six size skate sketch ski skill skin skirt sky slate sleep
slice slide slim slope slot slow small smart smile smoke smooth snack snake
snap snow soap social sock soft soil solar solid solve some song soon sort soul
sound soup source south space spare spark speak special speed spell spend
sphere spice spider spin spirit split spoke sponge spoon sport spot spray
spread spring sprout spruce square squash squid stable stack staff stage stair
stamp stand star start state stay steady steam steel stem step stick still
stitch stock stone stool stop store storm story stove strap straw stream
street strength stretch strike string strip strong studio study stuff style
subject submit subway success sudden sugar suit summer summit sun sunny super
supply support sure surf surge survey swan sweep sweet swift swim swing switch
sword symbol syrup system table tackle tactic tag tail tailor take tale talent
talk tall tank tape target task taste tavern tax tea teach team tear tech
temple tender tennis tent term test text thank theme theory thick thing think
third thorn thread three thrive throw thumb thunder ticket tidal tide tidy tie
tiger tile timber time tiny tip title toast today toe together token tomato
tone tongue tonight tool tooth top topic torch total touch tough tour tower
town toy trace track trade traffic trail train trait transfer trap travel tray
treat tree trend trial tribe trick trim trio trip trophy trouble truck true
trumpet trunk trust truth tube tulip tumble tuna tunnel turkey turn turtle
twelve twenty twice twin twist two type ultra umbrella uncle under unify union
unique unit unity universe unlock until upon upper upset urban urge usage use
useful usual valley value valve van vapor variety vast vault velvet vendor
venture venue verb verify version vessel veteran video view village vine
vintage violet violin virtue visible vision visit vital vivid vocal voice
volume vote voyage wage wagon waist wait wake walk wall walnut want warm warn
wash watch water wave wax way wealth weather weave wedge week weigh welcome
well west wet whale wheat wheel when where which while whisper white whole wide
width wild willow win wind window wine wing winter wire wisdom wise wish wolf
wonder wood wool word work world worry worth wrap wrist write yard yarn year
yellow yes yield yoga young zebra zero zone zoom
"""

#: ⚠ A tuple, and sorted, so the list is stable and the entropy claim above is
#: something a reader can check rather than take on trust.
WORDS: tuple[str, ...] = tuple(sorted(set(_RAW.split())))
