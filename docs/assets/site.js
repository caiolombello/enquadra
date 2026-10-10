'use strict';
const lang = document.documentElement.lang.startsWith('pt') ? 'pt' : 'en';
const descriptions = {
  pt: { original: 'Cena original: ambiente e enquadramento amplo.', auto: 'Simulação de recorte automático: a pessoa ganha espaço no quadro.', blur: 'Simulação de desfoque: o fundo perde detalhes, a pessoa fica em destaque.', image: 'Simulação de imagem de fundo: uma paisagem ilustrada substitui o ambiente.' },
  en: { original: 'Original scene: the room and a wide frame.', auto: 'Simulated automatic crop: the person takes more of the frame.', blur: 'Simulated blur: a softer background keeps the person in focus.', image: 'Simulated image background: an illustrated landscape replaces the room.' }
};
const scene = document.querySelector('.scene');
if (scene) {
  const controls = document.querySelector('.demo-controls');
  controls.hidden = false;
  controls.addEventListener('click', (event) => {
    const button = event.target.closest('button[data-mode]');
    if (!button) return;
    scene.dataset.mode = button.dataset.mode;
    controls.querySelectorAll('button').forEach((item) => item.setAttribute('aria-pressed', String(item === button)));
    document.querySelector('.demo-description').textContent = descriptions[lang][button.dataset.mode];
  });
}
const copy = document.querySelector('[data-copy]');
if (copy && navigator.clipboard && window.isSecureContext) {
  copy.hidden = false;
  copy.addEventListener('click', async () => {
    const status = document.querySelector('.copy-status');
    try {
      await navigator.clipboard.writeText(document.querySelector('#install-command').textContent);
      status.textContent = lang === 'pt' ? 'Comando copiado.' : 'Command copied.';
    } catch {
      status.textContent = lang === 'pt' ? 'Selecione e copie o comando acima.' : 'Select and copy the command above.';
    }
  });
}
