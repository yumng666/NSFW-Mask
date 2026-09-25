// 手机端 UI 增强：把「打码规则 / 检测灵敏度 / 遮罩形状」的原生 select 弹窗
// 替换为内联树形展开面板（点击字段 → 下方展开选项树，点选即生效并收起）。
// 仅窄屏启用；原 select 保留并隐藏，值与 change 事件完全同步，app.js 无感知。
(function () {
  'use strict';

  var MQ = window.matchMedia('(max-width: 700px)');
  var upgraded = []; // { select, wrapper }

  function selectedText(select) {
    var opt = select.options[select.selectedIndex];
    return opt ? opt.textContent : '';
  }

  function closeAll(except) {
    upgraded.forEach(function (u) {
      if (u.wrapper !== except) u.wrapper.classList.remove('open');
    });
  }

  function upgrade(select) {
    var wrapper = document.createElement('div');
    wrapper.className = 'tree-select';

    var btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'tree-btn';
    var label = document.createElement('span');
    label.className = 'tree-label';
    label.textContent = selectedText(select);
    var caret = document.createElement('span');
    caret.className = 'caret';
    caret.textContent = '▶';
    btn.appendChild(label);
    btn.appendChild(caret);

    var panel = document.createElement('div');
    panel.className = 'tree-panel';

    function rebuild() {
      panel.textContent = '';
      Array.prototype.forEach.call(select.options, function (opt) {
        var item = document.createElement('button');
        item.type = 'button';
        item.className = 'tree-opt' + (opt.selected ? ' active' : '');
        item.textContent = opt.textContent;
        item.addEventListener('click', function () {
          if (select.value !== opt.value) {
            select.value = opt.value;
            select.dispatchEvent(new Event('change', { bubbles: true }));
          }
          label.textContent = selectedText(select);
          wrapper.classList.remove('open');
        });
        panel.appendChild(item);
      });
    }

    btn.addEventListener('click', function (e) {
      e.stopPropagation();
      var willOpen = !wrapper.classList.contains('open');
      closeAll(wrapper);
      if (willOpen) {
        rebuild(); // 展开前重建，保证高亮与当前值一致
        wrapper.classList.add('open');
      } else {
        wrapper.classList.remove('open');
      }
    });

    select.addEventListener('change', function () {
      label.textContent = selectedText(select);
    });

    select.style.display = 'none';
    select.parentNode.insertBefore(wrapper, select);
    wrapper.appendChild(btn);      // 修复：按钮必须挂进容器，否则控件整个消失
    wrapper.appendChild(select);   // select 移入 wrapper，保持 DOM 邻近
    wrapper.appendChild(panel);

    upgraded.push({ select: select, wrapper: wrapper });
  }

  /** 模型加载横幅：/api/health 轮询，ready 前常驻顶部提示。 */
  function pollModelReady() {
    if (document.getElementById('model-bar')) return;
    var bar = document.createElement('div');
    bar.id = 'model-bar';
    bar.textContent = '模型加载中，首次启动需拷贝约 380MB，请稍候…';
    document.body.insertBefore(bar, document.body.firstChild);
    var n = 0;
    var t = setInterval(function () {
      fetch('/api/health').then(function (r) { return r.json(); }).then(function (d) {
        if (d && d.models_ready) {
          bar.remove();
          clearInterval(t);
        } else if (d && d.error) {
          bar.textContent = '模型加载失败：' + d.error;
          clearInterval(t);
        }
      }).catch(function () { /* 服务未起，继续等 */ });
      if (++n > 240) clearInterval(t);
    }, 500);
  }

  function init() {
    if (!MQ.matches) return;
    ['#ruleset', '#sensitivity', '#shapeMask'].forEach(function (sel) {
      var el = document.querySelector(sel);
      if (el && !upgraded.some(function (u) { return u.select === el; })) upgrade(el);
    });
    document.addEventListener('click', function () { closeAll(null); });
  }

  // 窗口从窄变宽时还原原生 select，避免桌面端出现自绘组件
  if (MQ.addEventListener) {
    MQ.addEventListener('change', function (e) {
      if (!e.matches) {
        upgraded.forEach(function (u) {
          u.wrapper.classList.remove('open');
          u.select.style.display = '';
          if (u.wrapper.parentNode) {
            u.wrapper.parentNode.insertBefore(u.select, u.wrapper);
            u.wrapper.parentNode.removeChild(u.wrapper);
          }
        });
        upgraded = [];
      }
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
  pollModelReady();
})();
