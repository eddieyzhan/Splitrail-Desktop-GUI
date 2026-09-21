import QtQuick
Canvas {
    id: icon
    property string name: "grid"
    property color color: AppStyle.ink
    implicitWidth: 20; implicitHeight: 20
    onNameChanged: requestPaint()
    onColorChanged: requestPaint()
    onWidthChanged: requestPaint()
    onPaint: {
        let c=getContext("2d"); c.reset(); c.scale(width/24,height/24)
        c.strokeStyle=icon.color; c.fillStyle=icon.color; c.lineWidth=1.65; c.lineCap="round"; c.lineJoin="round"
        function path(points, close) { c.beginPath(); c.moveTo(points[0][0],points[0][1]); for(let j=1;j<points.length;j++)c.lineTo(points[j][0],points[j][1]); if(close)c.closePath(); c.stroke() }
        function circle(x,y,r,fill) { c.beginPath(); c.arc(x,y,r,0,Math.PI*2); fill?c.fill():c.stroke() }
        if(name==="grid") { for(let x of [4,14]) for(let y of [4,14])c.strokeRect(x,y,6,6) }
        else if(name==="models") { path([[12,3],[21,8],[12,13],[3,8]],true); path([[3,12],[12,17],[21,12]]);path([[3,16],[12,21],[21,16]]) }
        else if(name==="tools") { c.strokeRect(4,6,16,14);path([[9,6],[9,3],[15,3],[15,6]]);path([[4,12],[20,12]]);path([[10,12],[10,15],[14,15],[14,12]]) }
        else if(name==="calendar") { c.strokeRect(4,5,16,16);path([[4,10],[20,10]]);path([[8,3],[8,7]]);path([[16,3],[16,7]]);circle(9,15,1,true);circle(15,15,1,true) }
        else if(name==="chart") { path([[4,19],[4,11]]);path([[10,19],[10,5]]);path([[16,19],[16,9]]);path([[22,19],[22,3]]) }
        else if(name==="quota") { circle(12,12,9,false);path([[12,3],[12,12],[19,17]]) }
        else if(name==="settings") { circle(12,12,3,false);circle(12,12,7,false);for(let a=0;a<6;a++){let t=a*Math.PI/3;path([[12+7*Math.cos(t),12+7*Math.sin(t)],[12+10*Math.cos(t),12+10*Math.sin(t)]])} }
        else if(name==="bell") { c.beginPath(); c.moveTo(5,17);c.lineTo(7,13);c.lineTo(7,8);c.bezierCurveTo(7,1,17,1,17,8);c.lineTo(17,13);c.lineTo(19,17);c.closePath();c.stroke();path([[10,21],[14,21]]) }
        else if(name==="refresh") { c.beginPath();c.arc(12,12,8,.5,5.2);c.stroke();path([[15,3],[17,6],[20,3]]) }
        else if(name==="left") path([[14,6],[8,12],[14,18]])
        else if(name==="right") path([[10,6],[16,12],[10,18]])
        else if(name==="down") path([[6,9],[12,15],[18,9]])
        else if(name==="check") path([[5,12],[10,17],[19,7]])
        else if(name==="close") {path([[6,6],[18,18]]);path([[6,18],[18,6]])}
        else if(name==="plus") {path([[12,5],[12,19]]);path([[5,12],[19,12]])}
        else if(name==="more") {circle(5,12,1,true);circle(12,12,1,true);circle(19,12,1,true)}
        else if(name==="cloud" || name==="github") {c.beginPath();c.moveTo(6,18);c.bezierCurveTo(-1,17,2,9,7,10);c.bezierCurveTo(6,0,20,1,19,11);c.bezierCurveTo(26,12,22,19,18,18);c.closePath();c.stroke()}
        else if(name==="lock") { c.strokeRect(5,10,14,11);c.beginPath();c.arc(12,10,5,Math.PI,0);c.stroke();circle(12,15,1,true);path([[12,15],[12,17]]) }
        else if(name==="device") {c.strokeRect(3,4,18,13);path([[8,21],[16,21]]);path([[12,17],[12,21]])}
        else if(name==="copy") {c.strokeRect(8,8,12,13);path([[5,16],[3,16],[3,3],[15,3],[15,5]])}
        else if(name==="search") {circle(10,10,6,false);path([[15,15],[21,21]])}
        else if(name==="external") {path([[13,3],[21,3],[21,11]]);path([[21,3],[11,13]]);path([[9,5],[4,5],[4,20],[19,20],[19,15]])}
        else if(name==="moon") { c.beginPath();c.arc(12,12,9,.3,4.5);c.quadraticCurveTo(4,15,20.6,14.6);c.stroke() }
        else if(name==="sun") {circle(12,12,4,false);for(let a=0;a<8;a++){let t=a*Math.PI/4;path([[12+7*Math.cos(t),12+7*Math.sin(t)],[12+9*Math.cos(t),12+9*Math.sin(t)]])}}
        else if(name==="logo") {c.lineWidth=2.1;path([[7,4],[7,20]]);path([[17,4],[17,20]]);path([[7,8],[17,8]]);path([[7,16],[17,16]])}
    }
}
