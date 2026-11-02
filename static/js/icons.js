const SUN = `
  <circle cx="25" cy="23" r="9.5" fill="#ffc857"/>
  <g stroke="#ffc857" stroke-width="2.4" stroke-linecap="round">
    <path d="M25 6v4M25 36v4M8 23h4M38 23h4M13 11l2.8 2.8M34.2 32.2 37 35M37 11l-2.8 2.8M15.8 32.2 13 35"/>
  </g>`;

const CLOUD = `
  <path d="M47 42H18a10 10 0 0 1-1.4-19.9A15 15 0 0 1 45.7 24 9.5 9.5 0 0 1 47 42Z"
        fill="currentColor" opacity=".92"/>`;

const CLOUD_SMALL = `
  <path d="M50 44H23a8.5 8.5 0 0 1-1.2-16.9A12.5 12.5 0 0 1 46.5 28 8 8 0 0 1 50 44Z"
        fill="currentColor" opacity=".92"/>`;

function drops(positions, color = "#43c8ff") {
  return positions
    .map(
      ([x, y]) =>
        `<path d="M${x} ${y}l-1.6 5" stroke="${color}" stroke-width="2.8" stroke-linecap="round"/>`
    )
    .join("");
}

function flakes(positions, color = "#cfefff") {
  return positions
    .map(([x, y]) => {
      return `<g stroke="${color}" stroke-width="1.8" stroke-linecap="round">
        <path d="M${x - 3} ${y}h6M${x} ${y - 3}v6M${x - 2} ${y - 2}l4 4M${x + 2} ${y - 2}l-4 4"/>
      </g>`;
    })
    .join("");
}

const ICONS = {
  sun: SUN,
  partly: `
    <g transform="translate(-3,-4) scale(.82)" opacity=".95">${SUN}</g>
    <g transform="translate(6,6)">${CLOUD_SMALL}</g>
  `,
  cloud: CLOUD,
  fog: `${CLOUD}
    <g stroke="currentColor" stroke-width="2.4" stroke-linecap="round" opacity=".65">
      <path d="M14 49h36M18 55h28"/>
    </g>`,
  drizzle: `${CLOUD}${drops([[22, 47], [32, 47], [42, 47]])}`,
  rain: `${CLOUD}${drops([[19, 46], [28, 50], [37, 46], [46, 50]])}`,
  showers: `${CLOUD}
    <g stroke="#ffc857" stroke-width="2.4" stroke-linecap="round">
      <path d="M30 44v8M26 52l-2 4M34 52l2 4"/>
    </g>
    ${drops([[20, 48], [43, 48]])}`,
  freeze: `${CLOUD}${drops([[26, 47]])}${flakes([[40, 51]])}`,
  snow: `${CLOUD}${flakes([[22, 49], [33, 53], [44, 49]])}`,
  storm: `
    ${CLOUD}
    <path d="M33 42h9l-6 8h6l-11 12 3-9h-6z" fill="#ffc857"/>
    ${drops([[18, 47], [46, 47]], "#43c8ff")}
  `,
};

export function weatherIcon(kind) {
  const content = ICONS[kind] || ICONS.cloud;
  return `<svg class="wi wi-${kind}" viewBox="0 0 64 64" aria-hidden="true" focusable="false">${content}</svg>`;
}

export function windArrow() {
  return `<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
    <path d="M12 21V4M5 11l7-7 7 7" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/>
  </svg>`;
}
