import { useState, useEffect, useCallback } from "react";

const OL = "#3D2B0A";
const SW = 3.8;

const SvgDefs = () => (
  <defs>
    <linearGradient id="bodyMain" x1="0" y1="0" x2="0.2" y2="1">
      <stop offset="0%" stopColor="#FFD54F"/><stop offset="50%" stopColor="#F5C033"/><stop offset="100%" stopColor="#E8AD20"/>
    </linearGradient>
    <linearGradient id="bodySide" x1="0" y1="0" x2="1" y2="0.5">
      <stop offset="0%" stopColor="#D9A020"/><stop offset="100%" stopColor="#C48E18"/>
    </linearGradient>
    <linearGradient id="bodyTop" x1="0" y1="1" x2="0.5" y2="0">
      <stop offset="0%" stopColor="#FFE082"/><stop offset="100%" stopColor="#FFF3B0"/>
    </linearGradient>
    <radialGradient id="holeGrad" cx="0.4" cy="0.4" r="0.6">
      <stop offset="0%" stopColor="#E8B830"/><stop offset="100%" stopColor="#C49518"/>
    </radialGradient>
    <radialGradient id="holeSideGrad" cx="0.4" cy="0.4" r="0.6">
      <stop offset="0%" stopColor="#CFA028"/><stop offset="100%" stopColor="#A88015"/>
    </radialGradient>
    <radialGradient id="eyeW" cx="0.45" cy="0.38" r="0.6">
      <stop offset="0%" stopColor="#FFFFFF"/><stop offset="100%" stopColor="#EEEEF5"/>
    </radialGradient>
    <radialGradient id="heartG" cx="0.4" cy="0.3" r="0.65">
      <stop offset="0%" stopColor="#FF4070"/><stop offset="100%" stopColor="#D02548"/>
    </radialGradient>
    <radialGradient id="mouthG" cx="0.5" cy="0.55" r="0.55">
      <stop offset="0%" stopColor="#E85555"/><stop offset="100%" stopColor="#B52828"/>
    </radialGradient>
    <radialGradient id="footG" cx="0.4" cy="0.35" r="0.6">
      <stop offset="0%" stopColor="#8B6530"/><stop offset="100%" stopColor="#5C4018"/>
    </radialGradient>
    <filter id="shadow" x="-8%" y="-5%" width="118%" height="118%">
      <feDropShadow dx="1.5" dy="3" stdDeviation="2.5" floodColor="#2A1A0540"/>
    </filter>
    <filter id="blurBlush" x="-40%" y="-40%" width="180%" height="180%">
      <feGaussianBlur stdDeviation="4"/>
    </filter>
  </defs>
);

const WedgeBody = () => (
  <g filter="url(#shadow)">
    <path d="M168,42 Q175,38 178,36 L185,40 L178,148 Q175,152 170,154 L165,155 Z" fill="url(#bodySide)" stroke={OL} strokeWidth={SW} strokeLinejoin="round"/>
    <circle cx={174} cy={75} r={5} fill="url(#holeSideGrad)" stroke="#A88018" strokeWidth={1.2}/>
    <circle cx={176} cy={115} r={4} fill="url(#holeSideGrad)" stroke="#A88018" strokeWidth={1.2}/>
    <path d="M100,18 Q105,15 112,16 L178,36 Q175,38 168,42 L100,28 Q96,25 100,18 Z" fill="url(#bodyTop)" stroke={OL} strokeWidth={SW} strokeLinejoin="round"/>
    <ellipse cx={140} cy={32} rx={6} ry={3.5} fill="#E8C840" stroke="#C8A828" strokeWidth={1} transform="rotate(-5,140,32)"/>
    <path d="M100,28 Q95,28 80,48 Q52,90 45,140 Q43,150 48,155 L165,155 Q170,152 168,142 L168,42 Q165,35 100,28 Z" fill="url(#bodyMain)" stroke={OL} strokeWidth={SW} strokeLinejoin="round" strokeLinecap="round"/>
    <circle cx={80} cy={60} r={12} fill="url(#holeGrad)" stroke="#C49820" strokeWidth={1.5}/>
    <circle cx={82} cy={57} r={5} fill="#E8C040" opacity={0.35}/>
    <circle cx={140} cy={65} r={9} fill="url(#holeGrad)" stroke="#C49820" strokeWidth={1.5}/>
    <circle cx={141} cy={63} r={4} fill="#E8C040" opacity={0.35}/>
    <circle cx={65} cy={105} r={8} fill="url(#holeGrad)" stroke="#C49820" strokeWidth={1.5}/>
    <circle cx={150} cy={110} r={10} fill="url(#holeGrad)" stroke="#C49820" strokeWidth={1.5}/>
    <circle cx={151} cy={108} r={4.5} fill="#E8C040" opacity={0.3}/>
    <circle cx={90} cy={140} r={6} fill="url(#holeGrad)" stroke="#C49820" strokeWidth={1.3}/>
    <path d="M100,30 Q95,32 85,48 Q80,58 78,65" fill="none" stroke="white" strokeWidth={2.5} opacity={0.2} strokeLinecap="round"/>
  </g>
);

const Legs = () => (
  <g>
    <rect x={85} y={153} width={15} height={18} rx={5} fill="#F0BE30" stroke={OL} strokeWidth={SW}/>
    <ellipse cx={92} cy={174} rx={13} ry={7} fill="url(#footG)" stroke={OL} strokeWidth={SW}/>
    <rect x={130} y={153} width={15} height={18} rx={5} fill="#F0BE30" stroke={OL} strokeWidth={SW}/>
    <ellipse cx={137} cy={174} rx={13} ry={7} fill="url(#footG)" stroke={OL} strokeWidth={SW}/>
  </g>
);

const Blush = () => (
  <g>
    <ellipse cx={75} cy={112} rx={10} ry={6.5} fill="#FF8090" filter="url(#blurBlush)" opacity={0.55}/>
    <ellipse cx={147} cy={112} rx={10} ry={6.5} fill="#FF8090" filter="url(#blurBlush)" opacity={0.5}/>
  </g>
);

const ArmsDown = () => (
  <g>
    <path d="M50,115 Q38,118 35,126 Q34,132 38,133 Q42,134 44,128 Q47,122 52,118" fill="#F0BE30" stroke={OL} strokeWidth={SW} strokeLinecap="round" strokeLinejoin="round"/>
    <path d="M165,118 Q175,120 178,128 Q179,134 175,135 Q171,136 169,130 Q167,124 165,120" fill="#D9A020" stroke={OL} strokeWidth={SW} strokeLinecap="round" strokeLinejoin="round"/>
  </g>
);
const ArmsUp = () => (
  <g>
    <path d="M55,70 Q40,60 35,48 Q33,42 37,40 Q41,38 43,44 Q47,55 55,64" fill="#F0BE30" stroke={OL} strokeWidth={SW} strokeLinecap="round" strokeLinejoin="round"/>
    <path d="M165,75 Q178,65 183,52 Q185,46 181,44 Q177,43 176,48 Q173,60 166,70" fill="#D9A020" stroke={OL} strokeWidth={SW} strokeLinecap="round" strokeLinejoin="round"/>
  </g>
);
const ArmsWave = () => (
  <g>
    <path d="M55,70 Q40,60 35,48 Q33,42 37,40 Q41,38 43,44 Q47,55 55,64" fill="#F0BE30" stroke={OL} strokeWidth={SW} strokeLinecap="round" strokeLinejoin="round"/>
    <path d="M165,118 Q175,120 178,128 Q179,134 175,135 Q171,136 169,130 Q167,124 165,120" fill="#D9A020" stroke={OL} strokeWidth={SW} strokeLinecap="round" strokeLinejoin="round"/>
  </g>
);
const ArmsCheeks = () => (
  <g>
    <path d="M55,100 Q42,95 38,88 Q36,82 40,80 Q44,79 45,84 Q47,92 55,97" fill="#F0BE30" stroke={OL} strokeWidth={SW} strokeLinecap="round" strokeLinejoin="round"/>
    <path d="M162,103 Q172,98 176,91 Q178,85 174,83 Q170,82 169,87 Q167,95 163,100" fill="#D9A020" stroke={OL} strokeWidth={SW} strokeLinecap="round" strokeLinejoin="round"/>
  </g>
);

const EyeNormal = () => (
  <g>
    <ellipse cx={95} cy={90} rx={14} ry={15} fill="url(#eyeW)" stroke={OL} strokeWidth={2.8}/>
    <ellipse cx={98} cy={91} rx={7} ry={8} fill="#1A1A1A"/>
    <circle cx={101} cy={86} r={3.5} fill="white" opacity={0.95}/>
    <circle cx={94} cy={95} r={1.8} fill="white" opacity={0.4}/>
    <ellipse cx={135} cy={92} rx={13} ry={14} fill="url(#eyeW)" stroke={OL} strokeWidth={2.8}/>
    <ellipse cx={138} cy={93} rx={6.5} ry={7.5} fill="#1A1A1A"/>
    <circle cx={141} cy={88} r={3.2} fill="white" opacity={0.95}/>
    <circle cx={134} cy={97} r={1.6} fill="white" opacity={0.4}/>
  </g>
);
const EyeExcited = () => (
  <g>
    <ellipse cx={95} cy={90} rx={14} ry={15} fill="url(#eyeW)" stroke={OL} strokeWidth={2.8}/>
    <ellipse cx={97} cy={90} rx={9} ry={10} fill="#1A1A1A"/>
    <circle cx={102} cy={85} r={4.5} fill="white" opacity={0.95}/>
    <circle cx={92} cy={95} r={2.5} fill="white" opacity={0.5}/>
    <ellipse cx={135} cy={92} rx={13} ry={14} fill="url(#eyeW)" stroke={OL} strokeWidth={2.8}/>
    <ellipse cx={137} cy={92} rx={8.5} ry={9.5} fill="#1A1A1A"/>
    <circle cx={142} cy={87} r={4} fill="white" opacity={0.95}/>
    <circle cx={132} cy={97} r={2.2} fill="white" opacity={0.5}/>
  </g>
);
const EyeSurprised = () => (
  <g>
    <ellipse cx={95} cy={88} rx={16} ry={17} fill="url(#eyeW)" stroke={OL} strokeWidth={2.8}/>
    <ellipse cx={98} cy={89} rx={6} ry={7} fill="#1A1A1A"/>
    <circle cx={101} cy={84} r={3.2} fill="white" opacity={0.9}/>
    <ellipse cx={135} cy={90} rx={15} ry={16} fill="url(#eyeW)" stroke={OL} strokeWidth={2.8}/>
    <ellipse cx={138} cy={91} rx={5.5} ry={6.5} fill="#1A1A1A"/>
    <circle cx={141} cy={86} r={3} fill="white" opacity={0.9}/>
  </g>
);
const EyeHeart = () => (
  <g>
    <g transform="translate(95,90) scale(0.9)">
      <path d="M0,-10 C-5,-18 -16,-18 -16,-8 C-16,0 0,12 0,12 C0,12 16,0 16,-8 C16,-18 5,-18 0,-10Z" fill="url(#heartG)" stroke={OL} strokeWidth={2.2}/>
      <ellipse cx={-4} cy={-7} rx={4} ry={3} fill="white" opacity={0.3} transform="rotate(-15)"/>
    </g>
    <g transform="translate(135,92) scale(0.85)">
      <path d="M0,-10 C-5,-18 -16,-18 -16,-8 C-16,0 0,12 0,12 C0,12 16,0 16,-8 C16,-18 5,-18 0,-10Z" fill="url(#heartG)" stroke={OL} strokeWidth={2.2}/>
      <ellipse cx={-4} cy={-7} rx={4} ry={3} fill="white" opacity={0.3} transform="rotate(-15)"/>
    </g>
  </g>
);
const EyeWink = () => (
  <g>
    <path d="M82,91 Q95,80 108,91" fill="none" stroke={OL} strokeWidth={3.5} strokeLinecap="round"/>
    <ellipse cx={135} cy={92} rx={13} ry={14} fill="url(#eyeW)" stroke={OL} strokeWidth={2.8}/>
    <ellipse cx={138} cy={93} rx={6.5} ry={7.5} fill="#1A1A1A"/>
    <circle cx={141} cy={88} r={3.2} fill="white" opacity={0.95}/>
    <circle cx={134} cy={97} r={1.6} fill="white" opacity={0.4}/>
  </g>
);
const EyeLaughing = () => (
  <g>
    <path d="M82,93 Q95,80 108,93" fill="none" stroke={OL} strokeWidth={3.5} strokeLinecap="round"/>
    <path d="M122,95 Q135,82 148,95" fill="none" stroke={OL} strokeWidth={3.5} strokeLinecap="round"/>
  </g>
);

const MouthHappy = () => (
  <g>
    <path d="M100,120 Q113,143 128,120 Z" fill="url(#mouthG)" stroke={OL} strokeWidth={2.5}/>
    <path d="M107,133 Q113,140 120,133" fill="#E87080"/>
  </g>
);
const MouthBig = () => (
  <g>
    <ellipse cx={113} cy={126} rx={17} ry={14} fill="url(#mouthG)" stroke={OL} strokeWidth={2.5}/>
    <path d="M101,132 Q113,143 125,132" fill="#E87080"/>
  </g>
);
const MouthO = () => (
  <ellipse cx={113} cy={124} rx={10} ry={13} fill="url(#mouthG)" stroke={OL} strokeWidth={2.5}/>
);
const MouthSmile = () => (
  <path d="M100,120 Q113,136 128,120" fill="none" stroke={OL} strokeWidth={3} strokeLinecap="round"/>
);
const MouthTongue = () => (
  <g>
    <path d="M100,120 Q113,136 128,120" fill="none" stroke={OL} strokeWidth={3} strokeLinecap="round"/>
    <ellipse cx={125} cy={130} rx={7} ry={5.5} fill="#E87080" stroke={OL} strokeWidth={1.5}/>
    <ellipse cx={124} cy={128} rx={3} ry={2} fill="#F5A0A8" opacity={0.5}/>
  </g>
);

const EXPRESSIONS = {
  happy:     { Eyes: EyeNormal,    Mouth: MouthHappy,  Arms: ArmsDown,   label: "Happy",     msg: "Let's gouda this!" },
  excited:   { Eyes: EyeExcited,   Mouth: MouthBig,    Arms: ArmsUp,     label: "Excited",   msg: "Orders are cheddar than ever!" },
  surprised: { Eyes: EyeSurprised, Mouth: MouthO,      Arms: ArmsWave,   label: "Surprised", msg: "Whoa, that's a big order!" },
  love:      { Eyes: EyeHeart,     Mouth: MouthSmile,  Arms: ArmsCheeks, label: "Love",      msg: "I brie-lieve in you!" },
  wink:      { Eyes: EyeWink,      Mouth: MouthTongue, Arms: ArmsWave,   label: "Wink",      msg: "Looking gouda today!" },
  laughing:  { Eyes: EyeLaughing,  Mouth: MouthBig,    Arms: ArmsDown,   label: "Laughing",  msg: "That's nacho average day!" },
};

const MOOD_COLORS = {
  happy: "#F5C033", excited: "#FF9E40", surprised: "#64B5F6",
  love: "#E83050",  wink: "#AB47BC",    laughing: "#66BB6A",
};

export default function CheeseMascot() {
  const moods = Object.keys(EXPRESSIONS);
  const [mood, setMood] = useState("happy");
  const [bounce, setBounce] = useState(false);

  const changeMood = useCallback((newMood) => {
    setMood(newMood);
    setBounce(true);
  }, []);

  useEffect(() => {
    if (bounce) {
      const t = setTimeout(() => setBounce(false), 400);
      return () => clearTimeout(t);
    }
  }, [bounce]);

  const cycleMood = () => {
    const i = moods.indexOf(mood);
    changeMood(moods[(i + 1) % moods.length]);
  };

  const { Eyes, Mouth, Arms, label, msg } = EXPRESSIONS[mood];

  return (
    <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 16, padding: 24, fontFamily: "'Segoe UI', system-ui, sans-serif" }}>
      <div style={{
        position: "relative", background: "#fff", border: `2px solid ${OL}`,
        borderRadius: 16, padding: "8px 16px", maxWidth: 240, textAlign: "center",
        fontSize: 14, fontWeight: 600, color: OL, boxShadow: "0 3px 10px rgba(0,0,0,0.12)"
      }}>
        {msg}
        <div style={{ position: "absolute", bottom: -10, left: "50%", transform: "translateX(-50%)", width: 0, height: 0, borderLeft: "8px solid transparent", borderRight: "8px solid transparent", borderTop: `10px solid ${OL}` }}/>
        <div style={{ position: "absolute", bottom: -7, left: "50%", transform: "translateX(-50%)", width: 0, height: 0, borderLeft: "7px solid transparent", borderRight: "7px solid transparent", borderTop: "9px solid #fff" }}/>
      </div>
      <div onClick={cycleMood} style={{
        cursor: "pointer",
        transform: bounce ? "scale(1.12) translateY(-10px)" : "scale(1)",
        transition: "transform 0.35s cubic-bezier(0.34, 1.56, 0.64, 1)",
      }}>
        <svg viewBox="0 0 220 190" width={220} height={190}>
          <SvgDefs/><Legs/><WedgeBody/><Arms/><Eyes/><Blush/><Mouth/>
        </svg>
      </div>
      <div style={{
        background: MOOD_COLORS[mood], color: "#fff", padding: "4px 18px",
        borderRadius: 20, fontSize: 13, fontWeight: 700, letterSpacing: 0.5,
        textTransform: "uppercase", boxShadow: `0 3px 8px ${MOOD_COLORS[mood]}50`
      }}>{label}</div>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", justifyContent: "center" }}>
        {moods.map((m) => (
          <button key={m} onClick={() => changeMood(m)} style={{
            padding: "6px 14px", borderRadius: 12, border: "2px solid",
            borderColor: mood === m ? MOOD_COLORS[m] : "#ddd",
            background: mood === m ? MOOD_COLORS[m] + "20" : "#fff",
            color: OL, fontSize: 12, fontWeight: 600, cursor: "pointer", transition: "all 0.2s",
          }}>{EXPRESSIONS[m].label}</button>
        ))}
      </div>
    </div>
  );
}
